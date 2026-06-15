from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare


class SaleCashierPayment(models.Model):
    _name = "sale.cashier.payment"
    _description = "Sales Cashier Payment"
    _order = "payment_date desc, id desc"

    name = fields.Char(
        string="Payment Number",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
    )
    order_id = fields.Many2one(
        "sale.order",
        string="Sales Order",
        required=True,
        ondelete="cascade",
        index=True,
    )
    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order",
        copy=False,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    partner_id = fields.Many2one(
        related="order_id.partner_id",
        string="Customer",
        store=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        related="order_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="order_id.currency_id",
        store=True,
        readonly=True,
    )
    amount = fields.Monetary(required=True)
    payment_method = fields.Selection(
        [
            ("cash", "Cash"),
            ("bank_transfer", "Bank Transfer"),
            ("card", "Debit/Credit Card"),
            ("pos", "Point of Sale"),
            ("other", "Other"),
        ],
        string="Payment Method",
        required=True,
        default="cash",
    )
    reference = fields.Char(string="Reference")
    payment_date = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
    )
    cashier_id = fields.Many2one(
        "res.users",
        string="Cashier",
        readonly=True,
        copy=False,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("confirmed", "Confirmed"), ("cancel", "Cancelled")],
        required=True,
        readonly=True,
        copy=False,
        default="draft",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["state"] = "draft"
            vals.pop("cashier_id", None)
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "sale.cashier.payment"
                ) or _("New")
        return super().create(vals_list)

    def write(self, vals):
        protected_fields = {
            "order_id",
            "amount",
            "payment_method",
            "reference",
            "payment_date",
            "cashier_id",
            "pos_order_id",
        }
        if protected_fields.intersection(vals) and self.filtered(
            lambda payment: payment.state != "draft"
        ):
            raise UserError(
                _("A confirmed or cancelled cashier payment cannot be modified.")
            )
        if "state" in vals and vals["state"] == "confirmed":
            raise UserError(_("Use Confirm Payment to confirm a cashier payment."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda payment: payment.state == "confirmed"):
            raise UserError(_("A confirmed cashier payment cannot be deleted."))
        return super().unlink()

    @api.constrains("amount")
    def _check_amount(self):
        for payment in self:
            if payment.amount <= 0:
                raise ValidationError(_("Payment amount must be greater than zero."))

    def action_confirm(self):
        if not self.env.user.has_group(
            "sale_cashier_checker.group_sales_cashier"
        ):
            raise UserError(_("Only a cashier can confirm a payment."))

        for payment in self:
            if payment.state != "draft":
                continue
            order = payment.order_id
            if order.state not in ("sale", "done"):
                raise UserError(
                    _("The sales order must be sent to the cashier first.")
                )
            if order.cashier_state == "paid":
                raise UserError(_("This sales order is already fully paid."))

            remaining = order.amount_due
            if float_compare(
                payment.amount,
                remaining,
                precision_rounding=payment.currency_id.rounding,
            ) > 0:
                raise UserError(
                    _("Payment cannot exceed the remaining amount of %s.")
                    % payment.currency_id.format(remaining)
                )
            super(SaleCashierPayment, payment).write(
                {
                    "state": "confirmed",
                    "cashier_id": self.env.user.id,
                    "payment_date": fields.Datetime.now(),
                }
            )
            order.sudo().message_post(
                body=_(
                    "Cashier payment %s confirmed for %s.",
                    payment.name,
                    payment.currency_id.format(payment.amount),
                ),
                author_id=self.env.user.partner_id.id,
            )
            order._send_deliveries_to_checker()
        return True

    @api.model
    def _create_from_pos_order(self, order, pos_order, amount):
        existing_payment = self.sudo().search(
            [
                ("order_id", "=", order.id),
                ("pos_order_id", "=", pos_order.id),
                ("state", "!=", "cancel"),
            ],
            limit=1,
        )
        if existing_payment:
            return existing_payment

        payment = self.sudo().create(
            {
                "order_id": order.id,
                "pos_order_id": pos_order.id,
                "amount": amount,
                "payment_method": "pos",
                "reference": pos_order.pos_reference or pos_order.name,
            }
        )
        super(SaleCashierPayment, payment).write(
            {
                "state": "confirmed",
                "cashier_id": pos_order.user_id.id or self.env.user.id,
                "payment_date": pos_order.date_order or fields.Datetime.now(),
            }
        )
        order.sudo().message_post(
            body=_(
                "POS payment %s confirmed for %s.",
                pos_order.pos_reference or pos_order.name,
                payment.currency_id.format(payment.amount),
            ),
            author_id=(pos_order.user_id or self.env.user).partner_id.id,
        )
        order._send_deliveries_to_checker()
        return payment

    def action_cancel(self):
        for payment in self:
            if payment.state == "confirmed" and not self.env.user.has_group(
                "sale_cashier_checker.group_sales_cashier_manager"
            ):
                raise UserError(
                    _("Only a cashier manager can cancel a confirmed payment.")
                )
        super(SaleCashierPayment, self).write({"state": "cancel"})
        return True

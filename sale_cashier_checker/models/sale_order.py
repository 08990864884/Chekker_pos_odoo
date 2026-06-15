from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class SaleOrder(models.Model):
    _inherit = "sale.order"

    cashier_checker_workflow = fields.Boolean(
        string="Cashier and Checker Workflow",
        copy=False,
        readonly=True,
    )
    cashier_payment_ids = fields.One2many(
        "sale.cashier.payment",
        "order_id",
        string="Cashier Payments",
    )
    cashier_payment_count = fields.Integer(
        compute="_compute_cashier_amounts",
        compute_sudo=True,
    )
    amount_paid = fields.Monetary(
        string="Paid Amount",
        compute="_compute_cashier_amounts",
        compute_sudo=True,
        store=True,
    )
    amount_due = fields.Monetary(
        string="Amount Due",
        compute="_compute_cashier_amounts",
        compute_sudo=True,
        store=True,
    )
    cashier_state = fields.Selection(
        [
            ("quotation", "Quotation"),
            ("waiting_payment", "Waiting Payment"),
            ("paid", "Paid"),
            ("cancel", "Cancelled"),
        ],
        string="Cashier Status",
        compute="_compute_cashier_amounts",
        compute_sudo=True,
        store=True,
    )

    @api.depends(
        "state",
        "order_line.invoice_status",
        "cashier_checker_workflow",
        "cashier_payment_ids.state",
        "cashier_payment_ids.pos_order_id",
    )
    def _compute_invoice_status(self):
        super()._compute_invoice_status()
        pos_settled_orders = self.filtered(
            lambda order: order.cashier_checker_workflow
            and any(
                payment.state == "confirmed" and payment.pos_order_id
                for payment in order.cashier_payment_ids
            )
        )
        pos_settled_orders.invoice_status = "no"

    @api.depends(
        "state",
        "amount_total",
        "currency_id",
        "cashier_payment_ids.amount",
        "cashier_payment_ids.state",
    )
    def _compute_cashier_amounts(self):
        for order in self:
            confirmed_payments = order.cashier_payment_ids.filtered(
                lambda payment: payment.state == "confirmed"
            )
            order.cashier_payment_count = len(order.cashier_payment_ids)
            order.amount_paid = sum(confirmed_payments.mapped("amount"))
            order.amount_due = max(order.amount_total - order.amount_paid, 0.0)

            if order.state == "cancel":
                order.cashier_state = "cancel"
            elif order.state in ("draft", "sent"):
                order.cashier_state = "quotation"
            elif float_compare(
                order.amount_paid,
                order.amount_total,
                precision_rounding=order.currency_id.rounding,
            ) >= 0:
                order.cashier_state = "paid"
            else:
                order.cashier_state = "waiting_payment"

    def action_send_to_cashier(self):
        for order in self:
            if order.state not in ("draft", "sent"):
                raise UserError(_("Only a quotation can be sent to the cashier."))
        self.action_confirm()
        for order in self:
            order.cashier_checker_workflow = True
            order.message_post(body=_("Sales order sent to the cashier."))
        return True

    def action_register_cashier_payment(self):
        self.ensure_one()
        if not self.env.user.has_group(
            "sale_cashier_checker.group_sales_cashier"
        ):
            raise UserError(_("Only a cashier can register a payment."))
        if self.state not in ("sale", "done"):
            raise UserError(_("Send the quotation to the cashier first."))
        if self.cashier_state == "paid":
            raise UserError(_("This sales order is already fully paid."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Register Cashier Payment"),
            "res_model": "sale.cashier.payment",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_order_id": self.id,
                "default_amount": self.amount_due,
            },
        }

    def action_view_cashier_payments(self):
        self.ensure_one()
        action = self.env.ref(
            "sale_cashier_checker.action_sale_cashier_payment"
        ).read()[0]
        action["domain"] = [("order_id", "=", self.id)]
        action["context"] = {"default_order_id": self.id}
        return action

    def _send_deliveries_to_checker(self):
        orders = self.sudo()
        checker_group = self.env.ref(
            "sale_cashier_checker.group_inventory_checker",
            raise_if_not_found=False,
        )
        checker_users = checker_group.users if checker_group else self.env["res.users"]
        for order in orders.filtered(
            lambda record: record.cashier_checker_workflow
            and record.cashier_state == "paid"
        ):
            pickings = order.picking_ids.filtered(
                lambda picking: picking.picking_type_code == "outgoing"
                and picking.state not in ("done", "cancel")
            )
            pickings.write({"checker_state": "waiting_check"})
            pickings.filtered(
                lambda picking: picking.state in ("confirmed", "waiting")
            ).action_assign()

            for picking in pickings:
                existing_users = picking.activity_ids.filtered(
                    lambda activity: activity.summary == "Check paid POS items"
                ).user_id
                for user in checker_users - existing_users:
                    picking.sudo().activity_schedule(
                        "mail.mail_activity_data_todo",
                        user_id=user.id,
                        summary=_("Check paid POS items"),
                        note=_(
                            "Payment is complete. Check the items for delivery %s, "
                            "then click Items Checked before validation.",
                            picking.name,
                        ),
                    )
        return True

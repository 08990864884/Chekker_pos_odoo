from odoo import _, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    checker_id = fields.Many2one(
        "res.users",
        string="Validated by Checker",
        readonly=True,
        copy=False,
    )
    checker_date = fields.Datetime(
        string="Checker Validation Date",
        readonly=True,
        copy=False,
    )
    sale_cashier_state = fields.Selection(
        related="sale_id.cashier_state",
        string="Cashier Status",
        readonly=True,
    )
    checker_state = fields.Selection(
        [
            ("waiting_check", "Waiting for Check"),
            ("checked", "Items Checked"),
            ("validated", "Validated"),
        ],
        string="Checker Status",
        copy=False,
        readonly=True,
    )
    checked_by_id = fields.Many2one(
        "res.users",
        string="Checked By",
        readonly=True,
        copy=False,
    )
    checked_date = fields.Datetime(
        string="Items Checked Date",
        readonly=True,
        copy=False,
    )

    def action_mark_items_checked(self):
        controlled_pickings = self.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.sale_id.cashier_checker_workflow
        )
        if self - controlled_pickings:
            raise UserError(
                _("This action is only available for cashier workflow deliveries.")
            )
        if not self.env.user.has_group(
            "sale_cashier_checker.group_inventory_checker"
        ):
            raise UserError(_("Only an Inventory Checker can mark items as checked."))
        if controlled_pickings.filtered(
            lambda picking: picking.sale_cashier_state != "paid"
        ):
            raise UserError(_("Items can only be checked after full payment."))
        controlled_pickings.write(
            {
                "checker_state": "checked",
                "checked_by_id": self.env.user.id,
                "checked_date": fields.Datetime.now(),
            }
        )
        controlled_pickings.sudo().activity_ids.filtered(
            lambda activity: activity.summary == "Check paid POS items"
        ).action_done()
        return True

    def button_validate(self):
        controlled_pickings = self.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.sale_id.cashier_checker_workflow
        )
        unpaid_pickings = controlled_pickings.filtered(
            lambda picking: picking.sale_cashier_state != "paid"
        )
        if unpaid_pickings:
            raise UserError(
                _(
                    "Delivery cannot be validated before cashier payment is fully paid: %s",
                    ", ".join(unpaid_pickings.mapped("name")),
                )
            )
        if controlled_pickings and not self.env.user.has_group(
            "sale_cashier_checker.group_inventory_checker"
        ):
            raise UserError(
                _("Only an Inventory Checker can validate a paid sales delivery.")
            )
        not_checked_pickings = controlled_pickings.filtered(
            lambda picking: picking.checker_state != "checked"
        )
        if not_checked_pickings:
            raise UserError(
                _(
                    "Mark Items Checked before validating these deliveries: %s",
                    ", ".join(not_checked_pickings.mapped("name")),
                )
            )

        controlled_pickings.write(
            {
                "checker_id": self.env.user.id,
                "checker_date": fields.Datetime.now(),
            }
        )
        result = super().button_validate()
        done_pickings = controlled_pickings.filtered(
            lambda picking: picking.state == "done"
        )
        done_pickings.write(
            {
                "checker_state": "validated",
            }
        )
        return result

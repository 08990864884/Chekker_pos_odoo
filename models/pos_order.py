from odoo import models
from odoo.tools.float_utils import float_compare


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _cashier_checker_sale_orders(self):
        self.ensure_one()
        return self.lines.mapped("sale_order_origin_id").filtered(
            "cashier_checker_workflow"
        )

    def action_pos_order_paid(self):
        result = super().action_pos_order_paid()
        for pos_order in self.filtered(lambda order: order.state == "paid"):
            sale_orders = pos_order._cashier_checker_sale_orders()
            if not sale_orders:
                continue

            # POS is the payment channel only. Delivery remains on the sales
            # picking and must still be validated by the Inventory Checker.
            linked_lines = pos_order.lines.filtered(
                lambda line: line.sale_order_origin_id in sale_orders
            )
            linked_lines.filtered("sale_order_line_id").sale_order_line_id = False

            for sale_order in sale_orders:
                pos_amount = sum(
                    linked_lines.filtered(
                        lambda line: line.sale_order_origin_id == sale_order
                    ).mapped("price_subtotal_incl")
                )
                amount = min(max(pos_amount, 0.0), sale_order.amount_due)
                if float_compare(
                    amount,
                    0.0,
                    precision_rounding=sale_order.currency_id.rounding,
                ) > 0:
                    self.env["sale.cashier.payment"]._create_from_pos_order(
                        sale_order,
                        pos_order,
                        amount,
                    )
        return result

    def _create_order_picking(self):
        self.ensure_one()
        workflow_orders = self._cashier_checker_sale_orders()
        stock_lines = self.lines.filtered(
            lambda line: line.product_id.type in ("product", "consu")
            and line.qty
        )
        if workflow_orders and stock_lines and all(
            line.sale_order_origin_id in workflow_orders for line in stock_lines
        ):
            return self.env["stock.picking"]
        return super()._create_order_picking()

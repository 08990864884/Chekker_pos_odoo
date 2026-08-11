from odoo.exceptions import UserError
from odoo import fields
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged("post_install", "-at_install")
class TestCashierCheckerFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sales_user = new_test_user(
            cls.env,
            login="workflow_sales",
            groups="sales_team.group_sale_salesman",
        )
        cls.cashier_user = new_test_user(
            cls.env,
            login="workflow_cashier",
            groups="sale_cashier_checker.group_sales_cashier",
        )
        cls.checker_user = new_test_user(
            cls.env,
            login="workflow_checker",
            groups="sale_cashier_checker.group_inventory_checker",
        )
        cls.partner = cls.env["res.partner"].create(
            {"name": "Workflow Test Customer"}
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Workflow Test Product",
                "type": "product",
                "list_price": 100.0,
            }
        )
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)],
            limit=1,
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.product,
            cls.warehouse.lot_stock_id,
            10.0,
        )

    def test_cashier_payment_then_checker_validation(self):
        order_form = Form(self.env["sale.order"].with_user(self.sales_user))
        order_form.partner_id = self.partner
        with order_form.order_line.new() as line:
            line.product_id = self.product
            line.product_uom_qty = 1.0
        order = order_form.save()

        order.action_send_to_cashier()
        self.assertIn(order.state, ("sale", "done"))
        self.assertEqual(order.cashier_state, "waiting_payment")

        picking = order.picking_ids.filtered(
            lambda record: record.picking_type_code == "outgoing"
        )[:1]
        with self.assertRaises(UserError):
            picking.with_user(self.checker_user).button_validate()

        payment = self.env["sale.cashier.payment"].with_user(
            self.cashier_user
        ).create(
            {
                "order_id": order.id,
                "amount": order.amount_total,
                "payment_method": "cash",
            }
        )
        payment.action_confirm()
        self.assertEqual(order.cashier_state, "paid")
        self.assertEqual(payment.cashier_id, self.cashier_user)

        with self.assertRaises(UserError):
            picking.with_user(self.sales_user).button_validate()

        with self.assertRaises(UserError):
            picking.with_user(self.checker_user).with_context(
                skip_sanity_check=True
            ).button_validate()

        picking.with_user(self.checker_user).action_mark_items_checked()
        picking.with_user(self.checker_user).with_context(
            skip_sanity_check=True
        ).button_validate()

        self.assertEqual(picking.checker_id, self.checker_user)

    def test_pos_settlement_keeps_sales_delivery_for_checker(self):
        session = self.env["pos.session"].search(
            [("state", "=", "opened")],
            limit=1,
        )
        if not session:
            session = self.env["pos.session"].create(
                {
                    "config_id": self.env["pos.config"].search([], limit=1).id,
                    "user_id": self.env.user.id,
                }
            )
            session.action_pos_session_open()
        payment_method = session.config_id.payment_method_ids[:1]
        self.assertTrue(payment_method)

        order_form = Form(self.env["sale.order"])
        order_form.partner_id = self.partner
        with order_form.order_line.new() as line:
            line.product_id = self.product
            line.product_uom_qty = 1.0
        order = order_form.save()
        order.action_send_to_cashier()
        sale_pickings = order.picking_ids
        sale_line = order.order_line.filtered(lambda line: not line.display_type)[:1]

        pos_order = self.env["pos.order"].create(
            {
                "session_id": session.id,
                "partner_id": self.partner.id,
                "pricelist_id": order.pricelist_id.id,
                "user_id": session.user_id.id,
                "date_order": fields.Datetime.now(),
                "amount_tax": order.amount_tax,
                "amount_total": order.amount_total,
                "amount_paid": 0.0,
                "amount_return": 0.0,
                "lines": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "qty": 1.0,
                            "price_unit": order.amount_total,
                            "price_subtotal": order.amount_untaxed,
                            "price_subtotal_incl": order.amount_total,
                            "discount": 0.0,
                            "tax_ids": [(6, 0, [])],
                            "sale_order_origin_id": order.id,
                            "sale_order_line_id": sale_line.id,
                            "full_product_name": self.product.display_name,
                        },
                    )
                ],
            }
        )
        pos_order.add_payment(
            {
                "pos_order_id": pos_order.id,
                "payment_method_id": payment_method.id,
                "amount": order.amount_total,
            }
        )
        pos_order.action_pos_order_paid()
        pos_order._create_order_picking()

        self.assertEqual(order.cashier_state, "paid")
        self.assertEqual(order.invoice_status, "no")
        self.assertFalse(pos_order.picking_ids)
        self.assertFalse(pos_order.lines.sale_order_line_id)
        self.assertTrue(all(picking.state != "cancel" for picking in sale_pickings))
        self.assertEqual(sale_line.qty_delivered, 0.0)
        checker_pickings = sale_pickings.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.state not in ("done", "cancel")
        )
        self.assertTrue(checker_pickings)
        self.assertTrue(
            all(picking.checker_state == "waiting_check" for picking in checker_pickings)
        )

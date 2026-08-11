/** @odoo-module **/

import { Order } from "point_of_sale.models";
import Registries from "point_of_sale.Registries";

const toNumber = (value) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : 0;
};

const CashierCheckerLoyaltyReceiptOrder = (Order) =>
    class extends Order {
        export_for_printing() {
            const receipt = super.export_for_printing(...arguments);
            const partner = this.get_partner();
            const standardStats = Array.isArray(receipt.loyaltyStats)
                ? receipt.loyaltyStats
                : [];
            const summaries = [];
            const handledProgramIds = new Set();

            if (partner && this.pos.getLoyaltyCards) {
                for (const card of this.pos.getLoyaltyCards(partner)) {
                    const program = this.pos.program_by_id[card.program_id];
                    if (!program || program.program_type !== "loyalty") {
                        continue;
                    }
                    const stat = standardStats.find(
                        (item) => item.program && item.program.id === program.id
                    );
                    summaries.push({
                        programName: program.name,
                        pointName: program.portal_point_name || "Poin",
                        won: stat ? toNumber(stat.points.won) : 0,
                        spent: stat ? toNumber(stat.points.spent) : 0,
                        balance: toNumber(card.balance),
                    });
                    handledProgramIds.add(program.id);
                }
            }

            // A newly-created loyalty card can still use its temporary local ID
            // in the order. Keep its transaction points visible even if it has
            // not yet been found in the partner card cache.
            for (const stat of standardStats) {
                const program = stat.program;
                if (
                    !program ||
                    program.program_type !== "loyalty" ||
                    handledProgramIds.has(program.id)
                ) {
                    continue;
                }
                const balance = toNumber(stat.points.balance);
                summaries.push({
                    programName: program.name,
                    pointName: program.portal_point_name || "Poin",
                    won: toNumber(stat.points.won),
                    spent: toNumber(stat.points.spent),
                    balance: balance || toNumber(stat.points.total),
                });
            }

            receipt.cashierCheckerLoyalty = summaries;
            receipt.cashierCheckerLoyaltyPartner = partner && partner.name;

            // Replace the conditional standard block with the summary below.
            // The standard block is hidden when the session has stale
            // `portal_visible` data, which is why points were missing here.
            receipt.loyaltyStats = false;
            return receipt;
        }
    };

Registries.Model.extend(Order, CashierCheckerLoyaltyReceiptOrder);

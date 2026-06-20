"""
Seed script — populates CardOffer table with real, current promotional offers
sourced from NDB Bank's public Card Offers page:
https://www.ndbbank.com/cards/card-offers

Run once after deploying:  python seed_card_offers.py
Re-running is safe — it skips offers that already exist (matched by title + bank_name).

All offers are publicly advertised promotions — no scraping automation,
manually curated and verified against the source page on the date below.
Source checked: 2026-06-20
"""

from datetime import date
from app import app, db
from models import CardOffer

# valid_until dates parsed from the bank's own listing text
SEED_OFFERS = [
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Flat 20% Savings on Any Destination Airlines (Base Fare)",
        "description": "0% installment plans up to 36 months on any airline destination booking.",
        "merchant": "findmyfare.com", "category": "Travel",
        "discount_pct": 20, "installment_months": "3,6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/247",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "0% Installment Plans up to 12 Months (Call & Convert)",
        "description": "Convert purchases to 0% interest installments up to 12 months.",
        "merchant": "Wimaladharma & Sons", "category": "Shopping",
        "discount_pct": 0, "installment_months": "3,6,12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/257",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plans on Educational Payments",
        "description": "For Platinum, Signature & Infinite cardholders on any educational payments.",
        "merchant": "Education IPP Promotion", "category": "Education",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/316",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "45% Savings on Reservations — Uga Bay Passikudah",
        "description": "Signature, Infinite & Privilege Banking cardholders.",
        "merchant": "Uga Bay - Passikudah", "category": "Travel",
        "discount_pct": 45, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/420",
    },
    {
        "bank_name": "NDB", "card_network": "Visa", "card_type": "Credit",
        "offer_type": "Reward", "title": "Complimentary Third Night on 3-Night Stays via Agoda",
        "description": "Visa Infinite cardholders booking 3+ nights on Agoda get the 3rd night free.",
        "merchant": "Agoda", "category": "Travel",
        "discount_pct": 0, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 12, 31),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/365",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 12 Months 0% on Hospital, Insurance & Auto Mobile Services",
        "description": "0% installment plans on hospital, insurance and auto services.",
        "merchant": "Special IPP Promotions", "category": "Medical",
        "discount_pct": 0, "installment_months": "3,6,12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/25",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Special Rate", "title": "Privilege Weekend June Edition",
        "description": "Signature, Infinite & Privilege Banking cardholders weekend privileges.",
        "merchant": "Privilege Weekend", "category": "Shopping",
        "discount_pct": 0, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/477",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 50% Savings on Reservations — Anantaya Chilaw",
        "description": "Stay reservation discount at Anantaya Resorts & Spa, Chilaw.",
        "merchant": "Anantaya Resorts & Spa - Chilaw", "category": "Travel",
        "discount_pct": 50, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/343",
    },
    {
        "bank_name": "NDB", "card_network": "Visa", "card_type": "Credit",
        "offer_type": "Special Rate", "title": "Explore Malaysia with Visa Offers",
        "description": "Exclusive Visa cardholder travel deals for Malaysia.",
        "merchant": "Visa", "category": "Travel",
        "discount_pct": 0, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 8, 31),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/367",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "25% Savings on Fresh Vegetables, Fruit, Fish & Meat",
        "description": "Discount on fresh produce, every 10th & 24th of the month.",
        "merchant": "Softlogic Glomark", "category": "Supermarket",
        "discount_pct": 25, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 24),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/104",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "0% Installment Plans up to 36 Months (Call & Convert)",
        "description": "Convert flight ticket purchases to 0% interest installments.",
        "merchant": "SriLankan Airlines", "category": "Travel",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/239",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plans on Jewellery",
        "description": "0% interest installment purchase plans on jewellery items.",
        "merchant": "Raja Jewellers", "category": "Shopping",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/259",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "40% Savings on Accelera Tyres, 30% on Pirelli/Yokohama/Otani",
        "description": "Tyre discounts with 0% installment plans for 12 months.",
        "merchant": "Toyota Lanka", "category": "Shopping",
        "discount_pct": 40, "installment_months": "12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/327",
    },
    {
        "bank_name": "NDB", "card_network": "Visa", "card_type": "Credit",
        "offer_type": "Special Rate", "title": "Unlock Premium Global Experiences with Visa",
        "description": "Exclusive global lifestyle experiences for Visa Signature & Infinite cardholders.",
        "merchant": "Visa Global", "category": "Travel",
        "discount_pct": 0, "installment_months": "", "interest_rate": 0,
        "valid_until": None,
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/379",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 35% on Diamond Jewellery, 30% on Colour Stones",
        "description": "Discounts on diamond, colour stone and stone-studded jewellery with 0% installments up to 36 months.",
        "merchant": "Aminra Jewellers", "category": "Shopping",
        "discount_pct": 35, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/260",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "12 Months 0% Installment Plans on Eye Care",
        "description": "0% interest installment plans on vision care services.",
        "merchant": "Vision Care", "category": "Medical",
        "discount_pct": 0, "installment_months": "12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/273",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "20% Savings on Reservations — Uga Prava Tangalle",
        "description": "Signature, Infinite & Privilege Banking cardholders booking period Apr–Jun 2026.",
        "merchant": "Uga Prava - Tangalle", "category": "Travel",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/418",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "All",
        "offer_type": "Discount", "title": "15% Savings on Credit Cards & 10% on Debit Cards",
        "description": "Flagship store-wide discount for credit and debit cardholders.",
        "merchant": "Emerald Flagship Store", "category": "Shopping",
        "discount_pct": 15, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 21),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/143",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Anything Anywhere — 25 Months 0% Plan, Thursdays",
        "description": "Platinum, Signature and Infinite cardholders — every Thursday until 25 Jun 2026.",
        "merchant": "Special IPP Promotions", "category": "Shopping",
        "discount_pct": 0, "installment_months": "25", "interest_rate": 0,
        "valid_until": date(2026, 6, 25),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/76",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plans (Call & Convert)",
        "description": "Convert purchases to 0% interest installments up to 36 months.",
        "merchant": "Singer / Singer Mega", "category": "Shopping",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/84",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 20% Savings with up to 36 Months 0% Installments",
        "description": "Discount on selected products plus 0% installment plans.",
        "merchant": "Dinapala Group", "category": "Shopping",
        "discount_pct": 20, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/93",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "12 Months 0% Installment Plans",
        "description": "0% interest installment plans on automotive purchases.",
        "merchant": "Stafford Motor Co.", "category": "Shopping",
        "discount_pct": 0, "installment_months": "12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/331",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "20% Savings on Total Bill",
        "description": "Bi-weekly discount on total bill — 14th & 28th of every month.",
        "merchant": "Softlogic Glomark", "category": "Supermarket",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 28),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/106",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plan (Call & Convert)",
        "description": "Convert furniture & electronics purchases to 0% interest installments.",
        "merchant": "Softlogic Furniture & Electronics", "category": "Shopping",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/88",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 50% Savings with up to 48 Months 0% Installments",
        "description": "Discount on selected electronics with extended installment plans.",
        "merchant": "Abans Elite & Retail Outlets", "category": "Shopping",
        "discount_pct": 50, "installment_months": "12,24,36,48", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/94",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 60% Savings with 12 Months 0% Installments",
        "description": "Selected items discount with 0% installment plan over 12 months.",
        "merchant": "Teleseen Marketing", "category": "Shopping",
        "discount_pct": 60, "installment_months": "12", "interest_rate": 0,
        "valid_until": date(2026, 7, 7),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/256",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 48 Months 0% Installment Plans",
        "description": "Online store purchases on extended 0% interest installment plans.",
        "merchant": "BigDeals.lk", "category": "Online",
        "discount_pct": 0, "installment_months": "12,24,36,48", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/348",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 20% Savings with up to 36 Months 0% Installments",
        "description": "Online store discount plus 0% installment plans.",
        "merchant": "Takas.lk", "category": "Online",
        "discount_pct": 20, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/500",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Cashback", "title": "100% Cashback on PickMe Pass Monthly Subscription",
        "description": "Full cashback on the PickMe Pass monthly subscription fee.",
        "merchant": "PickMe Pass", "category": "Online",
        "discount_pct": 0, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 12, 31),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/324",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "All",
        "offer_type": "Discount", "title": "20% Savings on Credit & 15% Savings on Debit Cards",
        "description": "Monthly recurring discount, 20th to month-end every month.",
        "merchant": "Hemas Consumer Brands", "category": "Shopping",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 12, 31),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/364",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "All",
        "offer_type": "Discount", "title": "20% Savings on Credit & 15% Savings on Debit Cards",
        "description": "Leather goods discount on select dates in June.",
        "merchant": "Leather Collection", "category": "Shopping",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 21),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/462",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "Up to 35% Savings on Selected Tyres, 20% on Wheel Alignment",
        "description": "Tyre and alignment discount with 12 months 0% installment plan.",
        "merchant": "Erosha Traders", "category": "Shopping",
        "discount_pct": 35, "installment_months": "12", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/328",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plans",
        "description": "Duty-free shopping with 0% interest installment plans.",
        "merchant": "Dufry", "category": "Shopping",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/355",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "All",
        "offer_type": "Discount", "title": "15% Discount for Bills Over Rs 10,000",
        "description": "Discount on clinic bills exceeding Rs. 10,000.",
        "merchant": "Siddhalepa Clinic", "category": "Medical",
        "discount_pct": 15, "installment_months": "", "interest_rate": 0, "min_spend": 10000,
        "valid_until": date(2026, 12, 31),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/441",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "20% Savings on Dinner Buffet (Mon–Thu)",
        "description": "Weekday dinner buffet discount at Colombo Kitchen.",
        "merchant": "Colombo Kitchen - Sheraton Colombo", "category": "Dining",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/490",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "20% Savings on Food",
        "description": "Dining discount at Bayu, Sheraton Colombo.",
        "merchant": "Bayu - Sheraton Colombo", "category": "Dining",
        "discount_pct": 20, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/491",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Discount", "title": "10% Savings on Wishque Cakes and Flower Arrangements",
        "description": "Limited-week discount on cakes and flower arrangements.",
        "merchant": "Wishque", "category": "Shopping",
        "discount_pct": 10, "installment_months": "", "interest_rate": 0,
        "valid_until": date(2026, 6, 19),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/389",
    },
    {
        "bank_name": "NDB", "card_network": "All", "card_type": "Credit",
        "offer_type": "Installment", "title": "Up to 36 Months 0% Installment Plans (Call & Convert)",
        "description": "Furniture purchases convertible to 0% interest installments.",
        "merchant": "Damro", "category": "Shopping",
        "discount_pct": 0, "installment_months": "6,12,24,36", "interest_rate": 0,
        "valid_until": date(2026, 6, 30),
        "source_url": "https://www.ndbbank.com/cards/card-offers/offer-details/87",
    },
]


def seed():
    with app.app_context():
        added = 0
        skipped = 0
        for offer in SEED_OFFERS:
            exists = CardOffer.query.filter_by(
                title=offer["title"], bank_name=offer["bank_name"], merchant=offer.get("merchant")
            ).first()
            if exists:
                skipped += 1
                continue

            card_offer = CardOffer(
                bank_name=offer["bank_name"],
                card_network=offer.get("card_network", "All"),
                card_type=offer.get("card_type", "All"),
                offer_type=offer["offer_type"],
                title=offer["title"],
                description=offer.get("description", ""),
                merchant=offer.get("merchant"),
                category=offer.get("category"),
                discount_pct=offer.get("discount_pct", 0),
                cashback_pct=offer.get("cashback_pct", 0),
                installment_months=offer.get("installment_months", ""),
                interest_rate=offer.get("interest_rate", 0),
                min_spend=offer.get("min_spend", 0),
                valid_from=offer.get("valid_from"),
                valid_until=offer.get("valid_until"),
                is_active=True,
                source_url=offer.get("source_url"),
                submitted_by="admin",
                status="approved",
                verified=True,
            )
            db.session.add(card_offer)
            added += 1

        db.session.commit()
        print(f"✅ Seeding complete — added {added} new offers, skipped {skipped} duplicates.")


if __name__ == "__main__":
    seed()

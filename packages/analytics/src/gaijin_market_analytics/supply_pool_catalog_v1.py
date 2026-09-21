from __future__ import annotations

from .supply_pool_catalog import SupplyPoolCatalog, SupplyPoolCatalogEvent, member, utc

# This catalog is intentionally vehicle-only. Every event below was transcribed
# from the official War Thunder event article's full vehicle list. Coupon mode is
# preserved because account-only returns do not inject tradeable Marketplace
# supply. Publication timestamps are exact only where an official forum timestamp
# was available. Publication-date-only official articles use a conservative
# end-of-day known_at; older entries without a publication timestamp may fall
# back to the event opening time.


# 2022 was the first year in the audited catalog where the recurring Silver-Lion
# reissue boxes are available from official complete vehicle lists.  These are
# kept because cumulative reissue exposure is materially different from the
# same-family recurrence model: an item may receive tradeable supply in one
# family and later migrate to another.
SPRING_2022 = (
    "Tiger (Germany)", "Kingcobra (USA)", "I-180S", "MPK Pr.122bis", "VFW",
    "Ju 388 J", "HMS Tiger", "Merkava Mk.1 (USA)", "Sd.Kfz.234/1",
    "SGB Grey Goose", "AU-1", "Object 279", "SU-76D", "JDS Yūgure (DD-184)",
    "F-4F Early (Germany)", "XM8",
)

SUMMER_2022 = (
    "Buccaneer S.1", "VL Myrsky II", "BMD-4", "Ka-Chi", "RN Aviere", "Bussard",
    "F11F-1", "Ar 196 A-3", "C2A1", "Ikv 73", "Maxim Gorky", "Freccia P-493",
    "BT-7A (F-32)", "HMS Spey", "Bf 110 C-6", "AUBL/74 HVG", "IJN Yuudachi",
    "P-59A", "FJ-4B VMF-232", "Martlet Mk IV (USA)", "Sd.Kfz.234/3", "IS-7",
    "M8 LAC", "Т-34E", "P-43A-1", "Ki-94-II", "Sd.Kfz.234/4",
    "Phòng không T-34 (USSR)", "F7F-3", "Fw 189 A-1", "KV-220", "Grant I",
)
SUMMER_2022_ACCOUNT_ONLY = {
    "VL Myrsky II", "Ka-Chi", "RN Aviere", "Ar 196 A-3", "Ikv 73",
    "Freccia P-493", "Phòng không T-34 (USSR)",
}

WINTER_2022 = (
    "MiG-23MF (Germany)", "C. 202D", "AGS", "A.C.I", "IJN Hayanami",
    "MPK Pr.201K", "MiG-21PFM", "Ro.57 Quadriarma", "Class 3 (P)",
    "E.B.R. (1954)", "USS Baltimore", "Type K-8 No.13", "TIS MA", "J6K1",
    "Merkava Mk.2B (USA)", "QF 3.7 Ram", "Jaguar", "PT-811", "Fw 190 C",
    "M.B.152C1", "T30", "SU-85A", "PBM-3 Mariner", "Leningrad", "P-40C",
    "NC.900", "Ersatz M10", "AEC Mk II", "Me 262 A-2a", "Ki-21-I hei",
    "LVT(A)(4) ZIS-2 (USA)", "Type 62 (USSR)", "P-63C-5 (USSR)",
    "VK 45.01 (P)", "M8A1 GMC", "I-301",
)
WINTER_2022_ACCOUNT_ONLY = {
    "C. 202D", "A.C.I", "IJN Hayanami", "Ro.57 Quadriarma", "E.B.R. (1954)",
    "Type K-8 No.13", "PBM-3 Mariner", "Leningrad", "NC.900", "Ersatz M10",
    "P-63C-5 (USSR)",
}

SPRING_2023 = (
    "Alecto I", "USS Davis", "Kfir C.2", "T-72M2 Moderna", "Pо-2М",
    "SMS Ostfriesland", "Jaguar E", "CCVL", "SU-76D", "JDS Yūgure (DD-184)",
    "F-4F Early (Germany)", "XM8", "Pbil m/40", "HMS Mohawk", "F-5A",
    "PT-16/T14 mod.", "Sd.Kfz.234/1", "SGB Grey Goose", "AU-1", "Object 279",
    "Sd.Kfz.251/10", "Lübeck", "BI", "Merkava Mk.3D (USA)", "I-180S",
    "MPK Pr.122bis", "VFW", "Ju 388 J", "HMS Tiger", "Merkava Mk.1 (USA)",
    "MBK-161 early", "Whirlwind P.9", "H-34", "VT1-2", "Tiger (Germany)",
    "Kingcobra (USA)",
)
SPRING_2024 = (
    "LOSAT", "F-100F (China)", "IJN Kurama", "Ki-48-II otsu", "QN506", "A-7K",
    "Shcherbakov", "AMD.35 (SA35)",
) + tuple(name for name in SPRING_2023 if name != "Whirlwind P.9")
SPRING_2025 = ("Object 292", "Jaguar IS", "SMS Baden", "PLZ83-130") + SPRING_2024
SPRING_2026_NEW = (
    "Toldi IIA", "Voroshilov", "P-39Q-25 (France)", "M6A2E1", "HMS Cadiz",
    "F-6C-10-NA", "Elefant", "S-701", "J26 David", "Centurion Mk.2",
    "USS Flagstaff", "P-51C-11-NT", "Kungstiger", "FV4030/3", "F-5A(G) (Sweden)",
    "Strasbourg", "FIROS 6", "Meteor FR.9 (Israel)", "Garford", "Mark V",
    "St-Chamond", "H.P.12", "Garford-Beute", "Beutepanzer IV", "A7V", "Amiens",
    "T-44 (FM)",
)
SPRING_2026 = SPRING_2026_NEW + SPRING_2025
SPRING_2026_ACCOUNT_ONLY = {
    "Toldi IIA", "Voroshilov", "HMS Cadiz", "S-701", "USS Flagstaff", "Garford",
    "Mark V", "St-Chamond", "H.P.12", "Garford-Beute", "Beutepanzer IV", "A7V",
}

SUMMER_2023 = (
    "Zrinyi I", "F4D-1", "Leopard 2AV", "IJN Haruna", "Buccaneer S.1",
    "VL Myrsky II", "BMD-4", "Ka-Chi", "RN Aviere", "Bussard", "F11F-1",
    "Ar 196 A-3", "C2A1", "Ikv 73", "Maxim Gorky", "Freccia P-493",
    "BT-7A (F-32)", "HMS Spey", "Bf 110 C-6", "AUBL/74 HVG", "IJN Yuudachi",
    "P-59A", "FJ-4B VMF-232", "Martlet Mk IV (Britain)", "Sd.Kfz.234/3",
    "IS-7", "Т-34E", "P-43A-1", "Ki-94-II", "Sd.Kfz.234/4",
    "Phòng không T-34 (USSR)", "F7F-3", "Fw 189 A-1", "KV-220", "Grant I",
)

SUMMER_2024 = (
    "Pz.Sp.Wg.P204(f) KwK", "CT-CV 105HP", "Su-25BM", "HMS Renown", "Zrinyi I",
    "F4D-1", "Leopard 2AV", "IJN Haruna", "Buccaneer S.1", "VL Myrsky II", "BMD-4",
    "Ka-Chi", "RN Aviere", "Bussard", "F11F-1", "Ar 196 A-3", "C2A1", "Ikv 73",
    "Maxim Gorky", "Freccia P-493", "BT-7A (F-32)", "HMS Spey", "Bf 110 C-6",
    "AUBL/74 HVG", "IJN Yuudachi", "P-59A", "FJ-4B VMF-232",
    "Martlet Mk IV (Britain)", "Sd.Kfz.234/3", "IS-7", "Т-34E", "P-43A-1",
    "Ki-94-II", "Sd.Kfz.234/4", "Phòng không T-34 (USSR)", "F7F-3", "Fw 189 A-1",
    "KV-220", "Grant I", "P-63A-10 (USSR)", "KV-IB (Germany)",
)
SUMMER_BASE_ACCOUNT_ONLY = {
    "Pz.Sp.Wg.P204(f) KwK", "Zrinyi I", "VL Myrsky II", "Ka-Chi", "RN Aviere",
    "Ar 196 A-3", "Ikv 73", "Freccia P-493", "Phòng không T-34 (USSR)",
    "P-63A-10 (USSR)",
}
SUMMER_2025 = ("F-14A IRIAF", "Alcione", "Churchill AVRE Petard") + SUMMER_2024
SUMMER_2026_NEW = (
    "Sd.Kfz.251/22", "USS Detroit", "A6M6c", "E.B.R. (1963)", "Type T-51a",
    "BV 155 B-1", "Object 248", "MC-485", "B7A2 (Homare 23)", "SPz 12-3 LGS",
    "PBM-5A Mariner", "Sholef", "SB-25J", "WZ141-1", "HF-24", "T86", "Shisaka",
    "F-106A",
)
SUMMER_2026 = SUMMER_2026_NEW + SUMMER_2025
SUMMER_2026_ACCOUNT_ONLY = SUMMER_BASE_ACCOUNT_ONLY | {
    "Sd.Kfz.251/22", "USS Detroit", "Type T-51a", "MC-485",
}

LUNAR_2024 = (
    "BM-31-12", "TOG II", "LVT(A)(4)", "Swordfish Mk II", "TKA-106", "He 51 B-2/H",
    "TB-3M-17-32", "LaGG-3-23", "DB-7 (Great Britain)", "XF5F", "I-29", "M8 LAC",
    "Ya-5M", "D.371 H.S.9", "A13 Mk II 1939", "38 cm Sturmmörser", "IS-2 No.321",
    "BA-11", "USS Phelps", "IL-2 M-82", "M60 AMBT", "AMX-50 Surblindé",
    "Spitfire F Mk IXc", "Hawk H-75A-2 (Germany)", "T18E2", "Lorraine 155 Mle.50",
    "Tandem MAI", "Whirlwind P.9", "PV-2D", "Yak-3T", 'MK-II "Matilda" (USSR)',
    "Excelsior", "KV-2 (1940)", 'Comet I "Iron Duke IV"', "Bong's P-38J-15",
    "MBR-2-M-34", "Spitfire Mk IXc (USSR)", "SU-76M (5th Gv.Kav.Corps)",
)
LUNAR_2024_ACCOUNT_ONLY = {
    "BM-31-12", "TOG II", "LVT(A)(4)", "Swordfish Mk II", "TKA-106", "TB-3M-17-32",
    "LaGG-3-23", "Ya-5M", "D.371 H.S.9", "38 cm Sturmmörser", "IS-2 No.321",
    "Lorraine 155 Mle.50", "Tandem MAI", "Yak-3T", "Spitfire Mk IXc (USSR)",
}
LUNAR_2025 = (
    "Eremin's Yak-3(e)", "KV-7 (U-13)",
) + tuple("TKA-412" if name == "TKA-106" else name for name in LUNAR_2024)
LUNAR_2025_ACCOUNT_ONLY = {
    *("TKA-412" if name == "TKA-106" else name for name in LUNAR_2024_ACCOUNT_ONLY),
    "KV-7 (U-13)",
}
LUNAR_2026_NEW = (
    "M46 (France)", "SB2C-5 (Japan)", "Le Triomphant", "IL-8 (1944)", "M551(76)",
    "Pr.183 BM-21", "Bf 109 F-4 (Italy)", "AMX-50 (TO90/930)", "Turan II", "MTB-422",
    "M64", "Su-8", "T55E1", "Z25", "ITP (M-1)", "Centurion Mk.5/1", "A-26C-45",
    "XF5U-1", 'T-34-85 "Partisan"',
)
LUNAR_2026 = LUNAR_2026_NEW + LUNAR_2025
LUNAR_2026_ACCOUNT_ONLY = LUNAR_2025_ACCOUNT_ONLY | {
    "Le Triomphant", "Pr.183 BM-21", "Turan II", "MTB-422", "T55E1", "Z25",
    "A-26C-45", "XF5U-1",
}

WINTER_2024 = (
    "Mirage 2000C-S4", "Vilkas", "USS Mississippi", "J9 Early", "Tornado IDS MFG",
    "T-80UM2", "USS Newport News", "Late 298D", "MiG-23MF (Germany)", "C. 202D", "AGS",
    "A.C.I", "IJN Hayanami", "MPK Pr.201K", "MiG-21PFM", "Ro.57 Quadriarma",
    "Class 3 (P)", "E.B.R. (1954)", "USS Baltimore", "Type K-8 No.13", "TIS MA", "J6K1",
    "Merkava Mk.2B (USA)", "QF 3.7 Ram", "Jaguar", "PT-811", "Fw 190 C", "M.B.152C1",
    "T30", "SU-85A", "PBM-3 Mariner", "Leningrad", "P-40C", "NC.900", "Ersatz M10",
    "AEC Mk II", "Me 262 A-2a", "Ki-21-I hei", "LVT(A)(4) ZIS-2 (USA)", "Type 62 (USSR)",
    "P-63C-5 (USSR)", "VK 45.01 (P)", "M8A1 GMC", "I-301",
)
WINTER_2023 = tuple(
    name
    for name in WINTER_2024
    if name not in {"Mirage 2000C-S4", "Vilkas", "USS Mississippi", "J9 Early"}
)

WINTER_2024_ACCOUNT_ONLY = {
    "J9 Early", "Late 298D", "C. 202D", "A.C.I", "IJN Hayanami", "Ro.57 Quadriarma",
    "E.B.R. (1954)", "Type K-8 No.13", "PBM-3 Mariner", "Leningrad", "NC.900",
    "Ersatz M10", "P-63C-5 (USSR)",
}
WINTER_2025 = ("T-80UD/BE", "Kfir C.10", "IJN Kirishima", "M.B.151C.1") + WINTER_2024
WINTER_2025_ACCOUNT_ONLY = WINTER_2024_ACCOUNT_ONLY | {"M.B.151C.1"}


def _members(names: tuple[str, ...], account_only: set[str] | frozenset[str] = frozenset()):
    aliases: dict[str, tuple[str, ...]] = {
        "PBM-3 Mariner": ('PBM-3 “Mariner”',),
        "Т-34E": ("T-34E",),
        "Pо-2М": ("Po-2M",),
    }
    return tuple(
        member(name, account_only=name in account_only, aliases=aliases.get(name, ()))
        for name in names
    )


EVENTS = (
    SupplyPoolCatalogEvent(
        event_key="spring_2022_april_chest",
        event_name="April's Chest",
        family="SPRING_CRAFT",
        known_at=utc(2022, 4, 1, 23, 59),
        opens_at=utc(2022, 4, 1, 9),
        closes_at=utc(2022, 4, 25, 11),
        source_url="https://warthunder.com/en/news/7632-event-children-of-arachis-desert-cinnamon-and-giant-worms-en",
        known_at_basis="official_article_publication_date_conservative",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=16,
        members=_members(SPRING_2022),
    ),
    SupplyPoolCatalogEvent(
        event_key="summer_2022_summer_cocktail",
        event_name="Summer Cocktail",
        family="SUMMER",
        known_at=utc(2022, 8, 3, 23, 59),
        opens_at=utc(2022, 8, 9, 11),
        closes_at=utc(2022, 8, 29, 12),
        source_url="https://warthunder.com/en/news/7796-current",
        known_at_basis="official_article_publication_date_conservative",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=32,
        members=_members(SUMMER_2022, SUMMER_2022_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="winter_2022_ice_chest",
        event_name="Ice Chest",
        family="WINTER",
        known_at=utc(2022, 12, 16, 23, 59),
        opens_at=utc(2022, 12, 20, 11),
        closes_at=utc(2023, 1, 9, 12),
        source_url="https://warthunder.com/en/news/8021-event-winter-quest-en",
        known_at_basis="official_article_publication_date_conservative",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=36,
        members=_members(WINTER_2022, WINTER_2022_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="spring_2023_lucky_pinata",
        event_name="Lucky Piñata",
        family="SPRING_CRAFT",
        known_at=utc(2023, 3, 23, 12),
        opens_at=utc(2023, 3, 23, 12),
        closes_at=utc(2023, 4, 17, 12),
        source_url="https://warthunder.com/en/news/8160-s",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=36,
        members=_members(SPRING_2023),
    ),
    SupplyPoolCatalogEvent(
        event_key="spring_2024_toolbox",
        event_name="The Toolbox",
        family="SPRING_CRAFT",
        known_at=utc(2024, 4, 8, 11),
        opens_at=utc(2024, 4, 8, 11),
        closes_at=utc(2024, 5, 6, 11),
        source_url="https://warthunder.com/en/news/8843-the-toolbox-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=43,
        members=_members(SPRING_2024),
    ),
    SupplyPoolCatalogEvent(
        event_key="spring_2025_infantry_backpack",
        event_name="The Infantry Backpack",
        family="SPRING_CRAFT",
        known_at=utc(2025, 4, 10, 11, 27),
        opens_at=utc(2025, 4, 10, 11),
        closes_at=utc(2025, 5, 7, 11),
        source_url="https://warthunder.com/en/news/9463-special-the-infantry-backpack-en",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/the-infantry-backpack/227863",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=47,
        members=_members(SPRING_2025),
    ),
    SupplyPoolCatalogEvent(
        event_key="spring_2026_nuclear_football",
        event_name="The Nuclear Football",
        family="SPRING_CRAFT",
        known_at=utc(2026, 4, 9, 11, 4),
        opens_at=utc(2026, 4, 9, 11),
        closes_at=utc(2026, 5, 6, 11),
        source_url="https://warthunder.com/en/news/9989-special-the-nuclear-football-en",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/the-nuclear-football/319814",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=74,
        members=_members(SPRING_2026, SPRING_2026_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="summer_2023_ice_cream_bucket",
        event_name="Ice Cream Bucket",
        family="SUMMER",
        known_at=utc(2023, 8, 4, 11, 21),
        opens_at=utc(2023, 8, 8, 11),
        closes_at=utc(2023, 9, 4, 12),
        source_url="https://warthunder.com/en/news/8384-e",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/summer-extreme/13528",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=35,
        members=_members(SUMMER_2023, SUMMER_BASE_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="summer_2024_travel_suitcase",
        event_name="The Travel Suitcase",
        family="SUMMER",
        known_at=utc(2024, 8, 8, 11),
        opens_at=utc(2024, 8, 8, 11),
        closes_at=utc(2024, 9, 4, 11),
        source_url="https://warthunder.com/en/news/9015-special-the-travel-suitcase-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=41,
        members=_members(SUMMER_2024, SUMMER_BASE_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="summer_2025_explosive_watermelon",
        event_name="The Explosive Watermelon",
        family="SUMMER",
        known_at=utc(2025, 8, 7, 11),
        opens_at=utc(2025, 8, 7, 11),
        closes_at=utc(2025, 9, 3, 11),
        source_url="https://warthunder.com/en/news/9628-special-the-explosive-watermelon-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=44,
        members=_members(SUMMER_2025, SUMMER_BASE_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="summer_2026_military_survival_kit",
        event_name="The Military Survival Kit",
        family="SUMMER",
        known_at=utc(2026, 8, 27, 11, 2),
        opens_at=utc(2026, 8, 27, 11),
        closes_at=utc(2026, 9, 25, 11),
        source_url="https://warthunder.com/en/news/10186-special-the-military-survival-kit-en",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/the-military-survival-kit/349366",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=62,
        members=_members(SUMMER_2026, SUMMER_2026_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="lunar_2024_dragon_box",
        event_name="The Dragon Box",
        family="HISTORY_LUNAR",
        known_at=utc(2024, 2, 1, 11),
        opens_at=utc(2024, 2, 1, 11),
        closes_at=utc(2024, 2, 28, 11),
        source_url="https://warthunder.com/en/news/8719-special-the-dragon-box-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=38,
        members=_members(LUNAR_2024, LUNAR_2024_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="lunar_2025_snake_box",
        event_name="The Snake Box",
        family="HISTORY_LUNAR",
        known_at=utc(2025, 1, 29, 11),
        opens_at=utc(2025, 1, 29, 11),
        closes_at=utc(2025, 2, 28, 11),
        source_url="https://warthunder.com/en/news/9347-the-snake-box-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=40,
        members=_members(LUNAR_2025, LUNAR_2025_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="lunar_2026_horseshoe_pouch",
        event_name="The Horseshoe Pouch",
        family="HISTORY_LUNAR",
        known_at=utc(2026, 2, 17, 11),
        opens_at=utc(2026, 2, 17, 11),
        closes_at=utc(2026, 3, 16, 11),
        source_url="https://warthunder.com/en/news/9899-special-the-horseshoe-pouch-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=59,
        members=_members(LUNAR_2026, LUNAR_2026_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="winter_2023_snowman",
        event_name="The Snowman",
        family="WINTER",
        known_at=utc(2023, 12, 20, 11, 24),
        opens_at=utc(2023, 12, 20, 12),
        closes_at=utc(2024, 1, 15, 12),
        source_url="https://warthunder.com/en/news/8644-event-winter-extreme-earn-yourself-rewards-this-festive-season-e",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/celebrate-christmas-and-the-new-year-in-war-thunder/58295",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=40,
        members=_members(WINTER_2023, WINTER_2024_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="winter_2024_snow_globe",
        event_name="The Snow Globe",
        family="WINTER",
        known_at=utc(2024, 12, 18, 11),
        opens_at=utc(2024, 12, 18, 11),
        closes_at=utc(2025, 1, 14, 11),
        source_url="https://warthunder.com/en/news/9289-event-the-snow-globe-for-silver-lions-en",
        known_at_basis="official_article_open_time_fallback",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=44,
        members=_members(WINTER_2024, WINTER_2024_ACCOUNT_ONLY),
    ),
    SupplyPoolCatalogEvent(
        event_key="winter_2025_festive_cupcake",
        event_name="The Festive Cupcake",
        family="WINTER",
        known_at=utc(2025, 12, 17, 11, 7),
        opens_at=utc(2025, 12, 17, 11),
        closes_at=utc(2026, 1, 13, 11),
        source_url="https://warthunder.com/en/news/9863-special-the-festive-cupcake-for-silver-lions-en",
        known_at_basis="official_forum_timestamp",
        known_at_source_url="https://forum.warthunder.com/t/the-festive-cupcake-for-silver-lions/292423",
        completeness="COMPLETE_VEHICLE_LIST",
        declared_vehicle_count=48,
        members=_members(WINTER_2025, WINTER_2025_ACCOUNT_ONLY),
    ),
)

DEFAULT_SUPPLY_POOL_CATALOG = SupplyPoolCatalog(version="1.2.0", events=EVENTS)

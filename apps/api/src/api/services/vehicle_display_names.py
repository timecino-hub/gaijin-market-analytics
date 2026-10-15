"""Verified presentation names; stored item identities remain unchanged."""

from sqlalchemy import case

from api.db.models import Item

DISPLAY_NAME_VERSION = "2026-10-08.names.1"
DISPLAY_NAMES = {
    "AMX 50 Surblindé (France)": "AMX-50 Surblindé",
    "id50268_kungstiger_sweden": "Kungstiger",
    "items_Т-10А (USSR)": "T-10A",
    "items_Merkava Mk.3D (USA)": "Merkava Mk.3D (USA)",
    "M8": "M8 LAC",
    "AUBL/74 HVG (Italy)": "AUBL/74 HVG",
    "BT-7 (F-32) (USSR)": "BT-7A (F-32)",
    "id50170_su_8_ussr": "Su-8",
    "id50257_object_292_ussr": "Object 292",
    "id50280_f_14a_iriaf_usa": "F-14A IRIAF (USA)",
    "id50294_◡kfir_c_10_israel": "Kfir C.10 (Israel)",
    "IS-7": "IS-7",
    "KV-220": "KV-220",
    "Object 279 (USSR)": "Object 279",
    "P-59A (USA)": "P-59A",
    "Sd.Kfz.234/4": "Sd.Kfz.234/4",
    "TIS MA (USSR)": "TIS MA",
    "id50148_bmd_4_ussr": "BMD-4",
    "id50163_ags_usa": "AGS",
    "id50190_leopard_2av_germany": "Leopard 2AV",
    "id50206_t_80um2_ussr": "T-80UM2",
    "id50230_ct_cv_105hp_sweden": "CT-CV 105HP",
    "id50232_su_25bm_ussr": "Su-25BM",
    "id50239_losat_usa": "LOSAT",
    "id50272_churchill_avre_petard_great_britain": "Churchill AVRE Petard",
    "id50292_t_80ud_be_china": "T-80UD/BE",
    "items_BI (USSR)": "BI",
    "items_Leopard C2A1 MEXAS (Germany)": "C2A1",
    "items_M60 AMBT (USA)": "M60 AMBT",
    "Me.262A-2a Sturmvogel": "Me 262 A-2a",
    "T18E2 Boarhound (USA)": "T18E2",
    "VFW (Germany)": "VFW",
    "id50255_m55176_usa": "M551(76)",
    "T-34E": "T-34E",
    "75mm Gun Motor Carriage M8": "M8A1 GMC",
    "KV-1B": "KV-IB (Germany)",
    "RBT-5": "RBT-5",
    "T-V": "T-V (USSR)",
    "id50145_e_b_r__1963_france": "E.B.R. (1963)",
    "id50184_object_248_ussr": "Object 248",
    "id50186_bv_155_b_1_germany": "BV 155 B-1",
    "id50228_spz_12_3_lgs_germany": "SPz 12-3 LGS",
    "id50276_pbm_5a_mariner_usa": 'PBM-5A "Mariner"',
    "id50288_he_177_a_3_germany": "He 177 A-3",
    "id50324_wz141_1_china": "WZ141-1",
    "id50330_f_106a_usa": "F-106A",
    "id50340_r400_germany": "R400",
    "id50345_crv_block_2_great_britain": "CRV Block 2",
    "id50347_mig_25pd_ussr": "MiG-25PD",
    "id50357_f_16a_france": "F-16A (France)",
    "id50361_f_6c_china": "F-6C (China)",
    "id50367_kv_8_ussr": "KV-8",
    "id50373_scimitar_mk_2_great_britain": "Scimitar Mk.2",
    "id50379_oktyabrskaya_revolutsiya_ussr": "Oktyabrskaya Revolutsiya",
    "items_Heavy Tank No.6 (Japan)": "Heavy Tank No.6 (Japan)",
}


def vehicle_display_name(external_key: str, fallback: str) -> str:
    return DISPLAY_NAMES.get(external_key, fallback)


def display_name_expression():
    return case(DISPLAY_NAMES, value=Item.external_key, else_=Item.name)

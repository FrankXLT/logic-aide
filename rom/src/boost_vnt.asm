; Cleanroom 68HC11 Wastegate Boost Control & Overboost / Rev-Limit Fuel-Cut Module
; Tables and constants match rom/models/boost_twin.py default_boost_calibration(2).

        .org    0xA000

;@table_start WG_DUTY_TBL
WG_DUTY_TBL:
        .byte   0x04
        .byte   0x20, 0x20, 0x02, 0x20
        .byte   0x40, 0x64, 0x01, 0x75
        .byte   0x70, 0xAA, 0x00, 0x60
        .byte   0xC0, 0xC8, 0x00, 0x00
;@table_end WG_DUTY_TBL

REV_LIMIT_U8:
        .byte   0xCB            ; 6500 RPM // 32 = 203
OVERBOOST_CUT_U8:
        .byte   0xFC            ; kpa_to_raw_map(200.0, 2) = 252
KNK_WG_GAIN_U8:
        .byte   0x18            ; Knock duty reduction (24 counts)
WG_MAX_DUTY_U8:
        .byte   0xC8            ; Maximum wastegate duty (200 = 100%)

; --- Boost & Limiter Code ---
        .org    0xA300

; CALC_WASTEGATE_BOOST: Compute WG duty and fuel-cutout flags
; Exit: A = RAM_WG_DUTY, B = RAM_CUTOUT_FLAGS
CALC_WASTEGATE_BOOST:
        LDAA    *RAM_RPM_HB
        LDX     #WG_DUTY_TBL
        JSR     LOOKUP4

        LDAB    *RAM_KNOCK_RETARD
        BEQ     CWB_CLAMP_WG
        SUBA    KNK_WG_GAIN_U8
        BCC     CWB_CLAMP_WG
        CLRA

CWB_CLAMP_WG:
        CMPA    WG_MAX_DUTY_U8
        BLS     CWB_STORE_WG
        LDAA    WG_MAX_DUTY_U8

CWB_STORE_WG:
        STAA    *RAM_WG_DUTY

        CLRB
        LDAA    *RAM_MAP_RAW
        CMPA    OVERBOOST_CUT_U8
        BCS     CWB_CHK_REV
        ORAB    #0x05           ; Overboost (bit 0) + fuel cut (bit 2)

CWB_CHK_REV:
        LDAA    *RAM_RPM_HB
        CMPA    REV_LIMIT_U8
        BCS     CWB_DONE
        ORAB    #0x06           ; Rev limit (bit 1) + fuel cut (bit 2)

CWB_DONE:
        STAB    *RAM_CUTOUT_FLAGS
        LDAA    *RAM_WG_DUTY
        RTS

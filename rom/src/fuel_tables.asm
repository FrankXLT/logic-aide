; Cleanroom 68HC11 Fueling Tables, Interpolation Helpers & Pulse Width Kernel
; Tables match rom/models/fuel_twin.py default_fuel_calibration(2) byte-for-byte.

        .org    0x8200

;@table_start BATOFF_TBL
BATOFF_TBL:
        .byte   0x04
        .byte   0x59, 0x6A, 0xFE, 0xD3
        .byte   0x81, 0x3B, 0xFF, 0x92
        .byte   0xC2, 0x1F, 0xFF, 0xC9
        .byte   0xFA, 0x13, 0x00, 0x00
;@table_end BATOFF_TBL

;@table_start PEFTBL
PEFTBL:
        .byte   0x0A
        .byte   0x4B, 0xDE, 0x00, 0x14
        .byte   0x58, 0xDF, 0x00, 0x95
        .byte   0x64, 0xE6, 0x00, 0x3B
        .byte   0x71, 0xE9, 0x00, 0x00
        .byte   0x86, 0xE9, 0xFF, 0x69
        .byte   0x9C, 0xDC, 0xFE, 0x80
        .byte   0xB2, 0xBB, 0xFE, 0xB0
        .byte   0xC2, 0xA6, 0x00, 0x00
        .byte   0xD7, 0xA6, 0x00, 0x00
        .byte   0xEA, 0xA6, 0x00, 0x00
;@table_end PEFTBL

;@table_start WOTTBL
WOTTBL:
        .byte   0x00, 0x00, 0x00, 0x19, 0xA3
        .byte   0x9D, 0x0F, 0xB9, 0x17, 0x57
        .byte   0xC6, 0x13, 0x76, 0x29, 0xA0
        .byte   0xCE, 0x14, 0xC3, 0x17, 0xA7
        .byte   0xFF, 0x19, 0x4A, 0x00, 0x00
        .byte   0xFF
;@table_end WOTTBL

MIN_PW_FLOOR_U16:
        .word   0x0060

; --- Fuel Math & Table Lookup Code ---
        .org    0x8600

; LOOKUP4: Piecewise-linear uint8 table interpolation
; Entry: A = x_u8, X = table_ptr ([N, x0, y0, s_hi, s_lo, ...])
; Exit:  A = B = y_u8
LOOKUP4:
        LDAB    0,X
        INX
        CMPA    0,X
        BHI     L4_FIND_SEG
        LDAA    1,X
        TAB
        RTS
L4_FIND_SEG:
        DECB
        BEQ     L4_AT_END
        CMPA    4,X
        BCS     L4_INTERP
        INX
        INX
        INX
        INX
        BRA     L4_FIND_SEG
L4_AT_END:
        LDAA    1,X
        TAB
        RTS
L4_INTERP:
        SUBA    0,X             ; A = delta = x - x_i
        PSHA
        LDAB    3,X             ; B = slope_lo
        MUL                     ; A = lo_prod >> 8, C = bit 7 rounding
        ADCA    #0x00           ; A = frac_adj
        PULB                    ; B = delta
        PSHA                    ; save frac_adj
        LDAA    2,X             ; A = slope_hi
        MUL                     ; B = (delta * slope_hi) & 0xFF
        PULA                    ; A = frac_adj
        ABA                     ; A = (frac_adj + lo(hi_prod)) & 0xFF
        ADDA    1,X             ; A = (y_sum_lo + y_i) & 0xFF
        TAB
        RTS

; SCALEY: Multiply uint16 Y by fractional uint8 A/256 with bit-7 rounding
; Entry: A = scale_u8, Y = val_u16
; Exit:  Y = scaled_u16 (X preserved)
SCALEY:
        PSHA
        PSHY
        PULB
        MUL
        XGDY
        PULB
        PULA
        MUL
        ADCA    #0x00
        TAB
        ABY
        RTS

; LOOKUP5: Piecewise-linear uint16 table interpolation
; Entry: A = x_u8, X = table_ptr ([x0, y0_hi, y0_lo, s_hi, s_lo, ..., 0xFF])
; Exit:  D = y_u16
LOOKUP5:
        CMPA    0,X
        BHI     L5_SEARCH
        LDD     1,X
        RTS
L5_SEARCH:
        CMPA    5,X
        BLS     L5_INTERP
        LDAB    #0x05
        ABX
        BRA     L5_SEARCH
L5_INTERP:
        SUBA    0,X             ; A = delta = x - x_i
        LDY     3,X             ; Y = slope_16
        JSR     SCALEY          ; Y = scale_y_by_a(delta, slope_16)
        XGDY
        ADDD    1,X             ; D = scaled_slope + y_i
        RTS

; SCALEX_ADDA: Scale *X by B/256, optionally add *X if A != 0, clamp 0xFFFF
; Entry: A = add_flag, B = scale_u8, X = ptr_to_u16
; Exit:  D = result_u16
SCALEX_ADDA:
        PSHA
        TBA
        LDY     0,X
        JSR     SCALEY
        PULA
        TSTA
        BEQ     SXA_NO_ADD
        XGDY
        ADDD    0,X
        BCC     SXA_DONE
        LDD     #0xFFFF
SXA_DONE:
        RTS
SXA_NO_ADD:
        XGDY
        RTS

; CALC_FUEL_PW: Compute injector pulse width (4 us ticks) into RAM_FUEL_PW_HB
CALC_FUEL_PW:
        LDAA    *RAM_MAP_RAW
        LDX     #WOTTBL
        JSR     LOOKUP5
        STD     *RAM_BASE_PW_HB

        LDAA    *RAM_RPM_HB
        LDX     #PEFTBL
        JSR     LOOKUP4
        STAA    *RAM_PEF_U8
        TSTA
        BNE     CFP_SCALE_PEF
        LDD     *RAM_BASE_PW_HB
        BRA     CFP_TRIM
CFP_SCALE_PEF:
        LDY     *RAM_BASE_PW_HB
        JSR     SCALEY
        XGDY

CFP_TRIM:
        STD     *RAM_TMP16_A
        LDAB    *RAM_FUEL_TRIM_U8
        BEQ     CFP_BATOFF
        LDAA    #0x01
        LDX     #RAM_TMP16_A
        JSR     SCALEX_ADDA
        STD     *RAM_TMP16_A

CFP_BATOFF:
        LDAA    *RAM_BATT_RAW
        LDX     #BATOFF_TBL
        JSR     LOOKUP4
        STAA    *RAM_BATOFF_U8
        CLRA
        ASLD
        ASLD                    ; D = latency_ticks = batoff * 4
        ADDD    *RAM_TMP16_A
        BCS     CFP_SATURATE
        CPD     MIN_PW_FLOOR_U16
        BCC     CFP_STORE
        LDD     MIN_PW_FLOOR_U16
        BRA     CFP_STORE
CFP_SATURATE:
        LDD     #0xFFFF
CFP_STORE:
        STD     *RAM_FUEL_PW_HB
        RTS

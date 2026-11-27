; Cleanroom 68HC11 Interrupt & Reset Vector Table ($FFC0-$FFFF)

        .org    0xFFC0

VECTORS_TBL:
        .word   ISR_NOP_RTI     ; $FFC0: Reserved
        .word   ISR_NOP_RTI     ; $FFC2: Reserved
        .word   ISR_NOP_RTI     ; $FFC4: Reserved
        .word   ISR_NOP_RTI     ; $FFC6: Reserved
        .word   ISR_NOP_RTI     ; $FFC8: Reserved
        .word   ISR_NOP_RTI     ; $FFCA: Reserved
        .word   ISR_NOP_RTI     ; $FFCC: Reserved
        .word   ISR_NOP_RTI     ; $FFCE: Reserved
        .word   ISR_NOP_RTI     ; $FFD0: Reserved
        .word   ISR_NOP_RTI     ; $FFD2: Reserved
        .word   ISR_NOP_RTI     ; $FFD4: Reserved
        .word   SCI_ISR         ; $FFD6: SCI Serial System
        .word   ISR_NOP_RTI     ; $FFD8: SPI Serial Transfer Complete
        .word   ISR_NOP_RTI     ; $FFDA: Pulse Accumulator Input Edge
        .word   ISR_NOP_RTI     ; $FFDC: Pulse Accumulator Overflow
        .word   ISR_NOP_RTI     ; $FFDE: Timer Overflow
        .word   TOC_ISR         ; $FFE0: Timer Input Capture 4 / Output Compare 5
        .word   TOC_ISR         ; $FFE2: Timer Output Compare 4
        .word   TOC_ISR         ; $FFE4: Timer Output Compare 3
        .word   TOC_ISR         ; $FFE6: Timer Output Compare 2
        .word   TOC_ISR         ; $FFE8: Timer Output Compare 1
        .word   ISR_NOP_RTI     ; $FFEA: Timer Input Capture 3
        .word   ISR_NOP_RTI     ; $FFEC: Timer Input Capture 2
        .word   ISR_NOP_RTI     ; $FFEE: Timer Input Capture 1
        .word   ISR_NOP_RTI     ; $FFF0: Real Time Interrupt
        .word   ISR_NOP_RTI     ; $FFF2: IRQ (External Pin)
        .word   ISR_NOP_RTI     ; $FFF4: XIRQ Pin
        .word   ISR_NOP_RTI     ; $FFF6: SWI
        .word   ISR_NOP_RTI     ; $FFF8: Illegal Opcode Trap
        .word   ISR_NOP_RTI     ; $FFFA: COP Failure
        .word   ISR_NOP_RTI     ; $FFFC: Clock Monitor Fail
        .word   RESET_ENTRY     ; $FFFE: Reset

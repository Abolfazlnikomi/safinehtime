(() => {
    const picker = window.jalaliDatepicker;
    if (!picker) return;

    picker.startWatch({
        autoShow: false,
        autoReadOnlyInput: false,
        persianDigits: true,
        showCloseBtn: true,
        changeMonthRotateYear: true,
        zIndex: 10000,
    });

    document.querySelectorAll('[data-record-calendar]').forEach(button => {
        const input = button.closest('.record-date-input').querySelector('input');
        button.addEventListener('click', event => {
            event.stopPropagation();
            const digits = '۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩';
            const normalized = input.value.trim().replace(/[۰-۹٠-٩]/g, digit => String(digits.indexOf(digit) % 10));
            const parts = normalized.match(/^(\d{4})([/-])(\d{1,2})\2(\d{1,2})$/);
            if (parts) input.value = `${parts[1]}/${parts[3].padStart(2, '0')}/${parts[4].padStart(2, '0')}`;
            input.focus({ preventScroll: true });
            picker.show(input);
        });
    });
})();

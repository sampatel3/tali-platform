export const COMPANY_CURRENCY_CODE = 'AED';
export const ASSESSMENT_PRICE_AED = 25;
export const aedToUsd = (aed) => (Number(aed || 0) * 0.27).toFixed(0);

export const formatAed = (amount, { minimumFractionDigits = 0, maximumFractionDigits = 0 } = {}) =>
  new Intl.NumberFormat('en-AE', {
    style: 'currency',
    currency: COMPANY_CURRENCY_CODE,
    minimumFractionDigits,
    maximumFractionDigits,
  }).format(Number.isFinite(Number(amount)) ? Number(amount) : 0);

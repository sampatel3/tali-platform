const PENDING_MARKETING_SECTION_KEY = 'taali.pendingMarketingSection';
const MARKETING_HEADER_OFFSET = 112;

const safeSessionStorage = () => {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

export const queuePendingMarketingSection = (sectionId) => {
  const storage = safeSessionStorage();
  if (!storage || !sectionId) return;
  storage.setItem(PENDING_MARKETING_SECTION_KEY, sectionId);
};

export const consumePendingMarketingSection = () => {
  const storage = safeSessionStorage();
  if (!storage) return '';
  const value = storage.getItem(PENDING_MARKETING_SECTION_KEY) || '';
  if (value) {
    storage.removeItem(PENDING_MARKETING_SECTION_KEY);
  }
  return value;
};

export const scrollToMarketingSection = (sectionId, { behavior = 'smooth' } = {}) => {
  if (typeof window === 'undefined' || !sectionId) return false;
  const target = document.getElementById(sectionId);
  if (!target) return false;

  const top = Math.max(
    0,
    window.scrollY + target.getBoundingClientRect().top - MARKETING_HEADER_OFFSET
  );
  window.scrollTo({ top, behavior });

  if (window.location.pathname === '/') {
    window.history.replaceState(null, '', `/#${sectionId}`);
  }

  return true;
};

export const navigateToMarketingSection = (sectionId, onNavigate) => {
  if (scrollToMarketingSection(sectionId)) return true;
  queuePendingMarketingSection(sectionId);
  if (typeof onNavigate === 'function') {
    onNavigate('landing');
  }
  return false;
};

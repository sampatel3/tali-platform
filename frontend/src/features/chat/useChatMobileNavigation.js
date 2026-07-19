import { useCallback, useEffect, useRef, useState } from 'react';

export const MOBILE_NAV_ID = 'chat-navigation-drawer';
const MOBILE_NAV_QUERY = '(max-width: 900px)';

// Keeps the off-canvas navigation's viewport state, focus trap, and focus
// restoration together so the page only consumes a small drawer contract.
export function useChatMobileNavigation({ agentRoleId, isAgents }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [isMobileDrawer, setIsMobileDrawer] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(MOBILE_NAV_QUERY).matches
  ));
  const rootRef = useRef(null);
  const mobileNavRef = useRef(null);
  const mobileNavReturnFocusRef = useRef(null);

  const openMobileNav = useCallback((event) => {
    const trigger = event?.currentTarget
      || (typeof document !== 'undefined' ? document.activeElement : null);
    if (trigger instanceof HTMLElement && trigger !== document.body) {
      mobileNavReturnFocusRef.current = trigger;
    }
    setMobileNavOpen(true);
  }, []);

  const closeMobileNav = useCallback((options = {}) => {
    const restoreFocus = options?.restoreFocus !== false;
    setMobileNavOpen(false);
    if (!restoreFocus || typeof window === 'undefined') return;

    const restore = () => {
      const trigger = mobileNavReturnFocusRef.current;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(restore);
    } else {
      window.setTimeout(restore, 0);
    }
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const media = window.matchMedia(MOBILE_NAV_QUERY);
    const sync = () => {
      setIsMobileDrawer(media.matches);
      if (!media.matches) setMobileNavOpen(false);
    };
    sync();
    if (typeof media.addEventListener === 'function') media.addEventListener('change', sync);
    else media.addListener?.(sync);
    return () => {
      if (typeof media.removeEventListener === 'function') media.removeEventListener('change', sync);
      else media.removeListener?.(sync);
    };
  }, []);

  useEffect(() => {
    if (!isMobileDrawer || !mobileNavOpen) return undefined;
    const drawer = mobileNavRef.current;
    if (!drawer) return undefined;

    drawer.focus({ preventScroll: true });
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeMobileNav();
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = Array.from(drawer.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true');
      if (!focusable.length) {
        event.preventDefault();
        drawer.focus({ preventScroll: true });
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [closeMobileNav, isMobileDrawer, mobileNavOpen]);

  // The agent surface owns its opener, so synchronize the drawer relationship
  // after route and viewport changes without duplicating its state.
  useEffect(() => {
    rootRef.current?.querySelectorAll('.cp-mobile-menu').forEach((button) => {
      button.setAttribute('aria-controls', MOBILE_NAV_ID);
      button.setAttribute('aria-expanded', String(isMobileDrawer && mobileNavOpen));
    });
  }, [agentRoleId, isAgents, isMobileDrawer, mobileNavOpen]);

  return {
    closeMobileNav,
    isMobileDrawer,
    mobileNavOpen,
    mobileNavRef,
    openMobileNav,
    rootRef,
  };
}

export default useChatMobileNavigation;

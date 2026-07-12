import {
  MOTION_DISTANCE,
  MOTION_DURATION,
  MOTION_EASE,
  MOTION_SPRING,
  MOTION_STAGGER,
} from './tokens';

export const motionTransition = Object.freeze({
  instant: Object.freeze({ duration: MOTION_DURATION.instant, ease: MOTION_EASE.standard }),
  fast: Object.freeze({ duration: MOTION_DURATION.fast, ease: MOTION_EASE.standard }),
  base: Object.freeze({ duration: MOTION_DURATION.base, ease: MOTION_EASE.standard }),
  spatial: Object.freeze({ duration: MOTION_DURATION.spatial, ease: MOTION_EASE.enter }),
  reveal: Object.freeze({ duration: MOTION_DURATION.reveal, ease: MOTION_EASE.enter }),
  data: Object.freeze({ duration: MOTION_DURATION.data, ease: MOTION_EASE.enter }),
  exit: Object.freeze({ duration: MOTION_DURATION.fast, ease: MOTION_EASE.exit }),
  layout: MOTION_SPRING.layout,
});

export const fadeVariants = Object.freeze({
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: motionTransition.base },
  exit: { opacity: 0, transition: motionTransition.exit },
});

export const fadeUpVariants = Object.freeze({
  hidden: { opacity: 0, y: MOTION_DISTANCE.medium },
  visible: { opacity: 1, y: 0, transition: motionTransition.reveal },
  exit: { opacity: 0, y: MOTION_DISTANCE.small, transition: motionTransition.exit },
});

export const fadeDownVariants = Object.freeze({
  hidden: { opacity: 0, y: -MOTION_DISTANCE.medium },
  visible: { opacity: 1, y: 0, transition: motionTransition.reveal },
  exit: { opacity: 0, y: -MOTION_DISTANCE.small, transition: motionTransition.exit },
});

export const scaleVariants = Object.freeze({
  hidden: { opacity: 0, scale: 0.98 },
  visible: { opacity: 1, scale: 1, transition: motionTransition.base },
  exit: { opacity: 0, scale: 0.98, transition: motionTransition.exit },
});

export const backdropVariants = Object.freeze({
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: motionTransition.base },
  exit: { opacity: 0, transition: motionTransition.exit },
});

export const dialogVariants = Object.freeze({
  hidden: { opacity: 0, y: MOTION_DISTANCE.medium, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: motionTransition.spatial },
  exit: { opacity: 0, y: MOTION_DISTANCE.small, scale: 0.98, transition: motionTransition.exit },
});

export const popoverVariants = Object.freeze({
  hidden: { opacity: 0, y: -MOTION_DISTANCE.small, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: motionTransition.base },
  exit: { opacity: 0, y: -MOTION_DISTANCE.micro, scale: 0.98, transition: motionTransition.exit },
});

export const createSheetVariants = (side = 'right') => {
  const horizontal = side === 'left' || side === 'right';
  const sign = side === 'left' || side === 'top' ? -1 : 1;
  const offscreen = `${sign * 100}%`;
  const hidden = horizontal ? { x: offscreen } : { y: offscreen };
  const visible = horizontal ? { x: 0 } : { y: 0 };

  return {
    hidden,
    visible: { ...visible, transition: motionTransition.spatial },
    exit: { ...hidden, transition: { duration: MOTION_DURATION.base, ease: MOTION_EASE.exit } },
  };
};

export const sheetVariants = Object.freeze(createSheetVariants('right'));

export const toastVariants = Object.freeze({
  hidden: { opacity: 0, y: -MOTION_DISTANCE.medium, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: motionTransition.spatial },
  exit: {
    opacity: 0,
    x: MOTION_DISTANCE.large,
    scale: 0.98,
    transition: motionTransition.exit,
  },
});

export const disclosureVariants = Object.freeze({
  collapsed: { height: 0, opacity: 0 },
  open: {
    height: 'auto',
    opacity: 1,
    transition: {
      height: motionTransition.spatial,
      opacity: motionTransition.base,
    },
  },
  exit: {
    height: 0,
    opacity: 0,
    transition: {
      height: { duration: MOTION_DURATION.base, ease: MOTION_EASE.exit },
      opacity: motionTransition.exit,
    },
  },
});

export const reducedFadeVariants = Object.freeze({
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: motionTransition.instant },
  exit: { opacity: 0, transition: motionTransition.instant },
});

export const cappedStaggerDelay = (index = 0, density = 'default') => {
  const step = density === 'dense' ? MOTION_STAGGER.dense : MOTION_STAGGER.default;
  const safeIndex = Math.max(0, Math.min(Number(index) || 0, MOTION_STAGGER.maxItems - 1));
  return safeIndex * step;
};

export const listContainerVariants = Object.freeze({
  hidden: {},
  visible: {},
  exit: {},
});

export const listItemVariants = Object.freeze({
  hidden: { opacity: 0, y: MOTION_DISTANCE.medium, scale: 0.99 },
  visible: ({ index = 0, density = 'default' } = {}) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    transition: {
      ...motionTransition.reveal,
      delay: cappedStaggerDelay(index, density),
    },
  }),
  exit: { opacity: 0, y: MOTION_DISTANCE.small, transition: motionTransition.exit },
});

export const createRevealVariants = ({ distance = MOTION_DISTANCE.medium, axis = 'y' } = {}) => ({
  hidden: { opacity: 0, [axis]: distance },
  visible: { opacity: 1, [axis]: 0 },
});

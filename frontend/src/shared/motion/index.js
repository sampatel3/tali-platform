// Application code imports Motion through this boundary. It keeps animation
// vocabulary consistent and lets lint:ui prevent one-off Motion dialects.
export {
  AnimatePresence,
  LayoutGroup,
  animate,
  m,
  stagger,
  useAnimate,
  useInView,
  useMotionValue,
  useMotionValueEvent,
} from 'motion/react';

export { MotionSystemProvider } from './MotionSystemProvider';
export { AgentFlowButton, AgentLoop, agentLoopPresets, resolveAgentLoop } from './agentLoops';
export {
  MOTION_EFFECT_DURATION,
  MotionLoop,
  MotionProgress,
  MotionSkeleton,
  MotionSpinner,
  motionLoopPresets,
  resolveMotionLoop,
} from './effects';
export {
  MotionDisclosure,
  MotionAttentionBadge,
  MotionList,
  MotionListItem,
  MotionNumber,
  MotionStagger,
  MotionTab,
  MotionTabs,
  PresenceSwap,
  Reveal,
} from './primitives';
export {
  REDUCED_MOTION_QUERY,
  motionSafeScrollBehavior,
  prefersReducedMotion,
  useReducedMotionSync,
} from './useReducedMotionSync';
export { useDocumentVisibility } from './useDocumentVisibility';

export {
  AGENT_LOOP_DURATION,
  MOTION_DISTANCE,
  MOTION_DURATION,
  MOTION_EASE,
  MOTION_SPRING,
  MOTION_STAGGER,
  agentLoopDuration,
  distance,
  duration,
  ease,
  motionTokens,
  spring,
  staggerToken,
} from './tokens';

export {
  backdropVariants,
  cappedStaggerDelay,
  createRevealVariants,
  createSheetVariants,
  dialogVariants,
  disclosureVariants,
  fadeDownVariants,
  fadeUpVariants,
  fadeVariants,
  listContainerVariants,
  listItemVariants,
  motionTransition,
  popoverVariants,
  reducedFadeVariants,
  scaleVariants,
  sheetVariants,
  toastVariants,
} from './presets';

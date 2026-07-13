import React from 'react';
import { LazyMotion, MotionConfig } from 'motion/react';

import { motionTransition } from './presets';

const loadMotionFeatures = () => import('./motionFeatures').then((module) => module.default);

/**
 * One Motion runtime and accessibility policy for the whole application.
 * `strict` keeps feature code on the lightweight `m` API and makes accidental
 * imports from a second Motion dialect visible during development.
 */
export function MotionSystemProvider({ children }) {
  return (
    <LazyMotion features={loadMotionFeatures} strict>
      <MotionConfig reducedMotion="user" transition={motionTransition.base}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}

export default MotionSystemProvider;

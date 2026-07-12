import React from 'react';
import { LazyMotion, MotionConfig, domMax } from 'motion/react';

import { motionTransition } from './presets';

/**
 * One Motion runtime and accessibility policy for the whole application.
 * `strict` keeps feature code on the lightweight `m` API and makes accidental
 * imports from a second Motion dialect visible during development.
 */
export function MotionSystemProvider({ children }) {
  return (
    <LazyMotion features={domMax} strict>
      <MotionConfig reducedMotion="user" transition={motionTransition.base}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}

export default MotionSystemProvider;

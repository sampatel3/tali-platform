const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const containsAny = (text, patterns) => patterns.some((pattern) => pattern.test(text));

const categoryPlaybook = {
  problem_framing: {
    label: 'Problem Framing',
    positive: 'Scoped the task clearly and focused on root cause.',
    improvement: 'State assumptions earlier and define success criteria up front.',
  },
  execution_rigor: {
    label: 'Execution Rigor',
    positive: 'Maintained steady progress with practical implementation steps.',
    improvement: 'Increase iterative checks while coding to reduce late surprises.',
  },
  testing_validation: {
    label: 'Testing & Validation',
    positive: 'Showed clear validation intent and quality checks.',
    improvement: 'Strengthen explicit test coverage and edge-case verification.',
  },
  ai_collaboration: {
    label: 'AI Collaboration',
    positive: 'Used assistant prompts to unblock efficiently and move forward.',
    improvement: 'Ask more precise AI prompts with concrete context and constraints.',
  },
  technical_communication: {
    label: 'Technical Communication',
    positive: 'Communicated decisions with clear technical context.',
    improvement: 'Explain tradeoffs and reasoning more explicitly in writing.',
  },
  delivery_momentum: {
    label: 'Delivery Momentum',
    positive: 'Kept momentum while balancing speed and quality.',
    improvement: 'Tighten iteration cadence and prioritize highest-impact fixes first.',
  },
};

export const signalLabelForLevel = (level) => {
  if (level >= 5) return 'Very strong';
  if (level >= 4) return 'Strong';
  if (level >= 3) return 'Developing';
  if (level >= 2) return 'Early';
  return 'Limited';
};

export const buildDemoSummary = ({
  runCount = 0,
  promptMessages = [],
  saveCount = 0,
  finalCode = '',
  timeSpentSeconds = 0,
  tabSwitchCount = 0,
}) => {
  const promptCount = promptMessages.length;
  const promptCorpus = promptMessages.join(' ').toLowerCase();
  const averagePromptLength = promptCount > 0
    ? promptMessages.reduce((acc, msg) => acc + msg.length, 0) / promptCount
    : 0;

  const hasTestingSignal = containsAny(`${promptCorpus} ${finalCode.toLowerCase()}`, [
    /test/i,
    /assert/i,
    /edge case/i,
    /coverage/i,
    /regression/i,
  ]);
  const hasDebugSignal = containsAny(promptCorpus, [/debug/i, /trace/i, /root cause/i, /investigate/i]);
  const hasTradeoffSignal = containsAny(promptCorpus, [/tradeoff/i, /risk/i, /impact/i, /constraint/i]);
  const hasStepSignal = containsAny(promptCorpus, [/step/i, /plan/i, /approach/i, /strategy/i]);
  const hasVerificationSignal = runCount >= 2 || hasTestingSignal;

  const categories = [
    {
      key: 'problem_framing',
      level: clamp(1 + (hasStepSignal ? 1 : 0) + (hasDebugSignal ? 1 : 0) + (averagePromptLength > 55 ? 1 : 0), 1, 5),
    },
    {
      key: 'execution_rigor',
      level: clamp(1 + Math.min(runCount, 3) + (saveCount > 0 ? 1 : 0), 1, 5),
    },
    {
      key: 'testing_validation',
      level: clamp(1 + (hasTestingSignal ? 2 : 0) + (hasVerificationSignal ? 1 : 0), 1, 5),
    },
    {
      key: 'ai_collaboration',
      level: clamp(1 + Math.min(promptCount, 3) + (averagePromptLength > 30 ? 1 : 0), 1, 5),
    },
    {
      key: 'technical_communication',
      level: clamp(1 + (averagePromptLength > 45 ? 1 : 0) + (hasTradeoffSignal ? 2 : 0), 1, 5),
    },
    {
      key: 'delivery_momentum',
      level: clamp(1 + (timeSpentSeconds > 180 ? 1 : 0) + (runCount > 1 ? 1 : 0) + (promptCount > 0 ? 1 : 0), 1, 5),
    },
  ].map((entry) => ({
    ...entry,
    label: categoryPlaybook[entry.key].label,
    signal: signalLabelForLevel(entry.level),
  }));

  const sorted = [...categories].sort((a, b) => b.level - a.level);
  const highlights = sorted.slice(0, 3).map((entry) => ({
    key: entry.key,
    label: entry.label,
    signal: entry.signal,
    text: categoryPlaybook[entry.key].positive,
  }));
  const opportunities = [...sorted].reverse().slice(0, 2).map((entry) => ({
    key: entry.key,
    label: entry.label,
    signal: entry.signal,
    text: categoryPlaybook[entry.key].improvement,
  }));

  return {
    categories,
    highlights,
    opportunities,
    meta: {
      promptCount,
      runCount,
      saveCount,
      timeSpentSeconds,
      tabSwitchCount,
    },
  };
};

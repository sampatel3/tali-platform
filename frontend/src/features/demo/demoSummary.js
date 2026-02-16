const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const containsAny = (text, patterns) => patterns.some((pattern) => pattern.test(text));

const categoryPlaybook = {
  problem_framing: {
    label: 'Problem Framing',
  },
  execution_rigor: {
    label: 'Execution Rigor',
  },
  testing_validation: {
    label: 'Testing & Validation',
  },
  ai_collaboration: {
    label: 'AI Collaboration',
  },
  technical_communication: {
    label: 'Technical Communication',
  },
  delivery_momentum: {
    label: 'Delivery Momentum',
  },
};

const successfulCandidateBenchmarks = {
  data_eng_a_pipeline_reliability: {
    problem_framing: 3.9,
    execution_rigor: 3.8,
    testing_validation: 3.9,
    ai_collaboration: 3.8,
    technical_communication: 3.7,
    delivery_momentum: 3.7,
  },
  data_eng_b_cdc_fix: {
    problem_framing: 3.7,
    execution_rigor: 3.8,
    testing_validation: 3.5,
    ai_collaboration: 3.6,
    technical_communication: 3.4,
    delivery_momentum: 3.8,
  },
  data_eng_c_backfill_schema: {
    problem_framing: 3.8,
    execution_rigor: 3.6,
    testing_validation: 3.7,
    ai_collaboration: 3.5,
    technical_communication: 3.6,
    delivery_momentum: 3.5,
  },
  default: {
    problem_framing: 3.6,
    execution_rigor: 3.6,
    testing_validation: 3.5,
    ai_collaboration: 3.5,
    technical_communication: 3.4,
    delivery_momentum: 3.6,
  },
};

const levelToScore = (level) => Math.round((clamp(Number(level) || 0, 0, 5) / 5) * 100);

export const profileBandForLevel = (level) => {
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
  taskKey = null,
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
    band: profileBandForLevel(entry.level),
  }));

  const benchmarkByCategory = successfulCandidateBenchmarks[taskKey] || successfulCandidateBenchmarks.default;
  const comparisonCategories = categories.map((entry) => {
    const benchmarkLevel = clamp(
      Number(benchmarkByCategory[entry.key] ?? successfulCandidateBenchmarks.default[entry.key] ?? 3.5),
      1,
      5,
    );
    const deltaLevel = Number((entry.level - benchmarkLevel).toFixed(1));
    return {
      key: entry.key,
      label: entry.label,
      candidateLevel: entry.level,
      benchmarkLevel,
      deltaLevel,
      candidateScore: levelToScore(entry.level),
      benchmarkScore: levelToScore(benchmarkLevel),
    };
  });

  const candidateAvgLevel = categories.reduce((acc, entry) => acc + entry.level, 0) / Math.max(categories.length, 1);
  const benchmarkAvgLevel = comparisonCategories.reduce((acc, entry) => acc + entry.benchmarkLevel, 0) / Math.max(comparisonCategories.length, 1);
  const candidateScore = levelToScore(candidateAvgLevel);
  const benchmarkScore = levelToScore(benchmarkAvgLevel);

  return {
    categories,
    comparison: {
      candidateScore,
      benchmarkScore,
      deltaScore: candidateScore - benchmarkScore,
      categories: comparisonCategories,
      benchmarkLabel: 'Successful-candidate average',
    },
    meta: {
      promptCount,
      runCount,
      saveCount,
      timeSpentSeconds,
      tabSwitchCount,
    },
  };
};

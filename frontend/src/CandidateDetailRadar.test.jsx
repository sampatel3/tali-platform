import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { CandidateDetailPage } from './App';

vi.mock('./context/AuthContext', () => ({
  useAuth: () => ({
    user: { organization: { name: 'Org' } },
    logout: vi.fn(),
  }),
}));

vi.mock('./shared/api', () => ({
  assessments: {
    downloadReport: vi.fn(),
    postToWorkable: vi.fn(),
    remove: vi.fn(),
    addNote: vi.fn(),
  },
  organizations: {},
  tasks: {},
  analytics: {
    get: vi.fn().mockResolvedValue({ data: { avg_calibration_score: 6.2 } }),
  },
  billing: {},
  team: {},
  candidates: {},
}));

describe('CandidateDetailPage radar chart', () => {
  it('renders scoring dimensions in AI Usage tab', async () => {
    const candidate = {
      name: 'Jane Doe',
      email: 'jane@example.com',
      position: 'Engineer',
      task: 'Debugging',
      time: '30m',
      score: 8.4,
      completedDate: 'Today',
      breakdown: {
        bugsFixed: '3/3',
        testsPassed: '5/5',
        codeQuality: 8.0,
        timeEfficiency: 7.8,
        aiUsage: 8.2,
      },
      results: [{ title: 'Result', score: '5/5', description: 'ok' }],
      promptsList: [{ text: 'help' }],
      timeline: [{ time: '00:00', event: 'Started' }],
      _raw: {
        id: 1,
        prompt_quality_score: 8.0,
        prompt_efficiency_score: 7.0,
        independence_score: 6.0,
        context_utilization_score: 7.0,
        design_thinking_score: 6.5,
        debugging_strategy_score: 7.5,
        written_communication_score: 8.0,
        learning_velocity_score: 7.5,
        error_recovery_score: 6.5,
        requirement_comprehension_score: 7.0,
        calibration_score: 6.0,
        browser_focus_ratio: 0.9,
        tab_switch_count: 1,
        prompt_analytics: {
          ai_scores: { prompt_specificity: 7.0, prompt_progression: 8.0 },
          component_scores: { tests: 8.0, prompt_quality: 8.0 },
          weights_used: { tests: 0.3, prompt_quality: 0.15 },
          per_prompt_scores: [{ clarity: 7, specificity: 8, efficiency: 7 }],
        },
        prompt_fraud_flags: [],
      },
    };

    render(
      <CandidateDetailPage
        candidate={candidate}
        onNavigate={vi.fn()}
        onDeleted={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'AI Usage' }));

    // Check for elements in the redesigned AI Usage tab
    expect(await screen.findByText(/Avg Prompt clarity/)).toBeInTheDocument();
    expect(screen.getByText(/Prompt clarity progression/)).toBeInTheDocument();
  });
});

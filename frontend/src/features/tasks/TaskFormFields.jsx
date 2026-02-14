import React from 'react';

export const TaskFormFields = ({ form, setForm, readOnly = false }) => {
  const noop = () => {};
  const upd = readOnly ? noop : setForm;
  const inputClass = (base) => `${base} ${readOnly ? 'bg-gray-100 cursor-default' : ''}`;

  return (
    <div className="space-y-4">
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Task Name *</label>
        <input
          type="text"
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
          placeholder="e.g. Async Pipeline Debugging"
          value={form.name}
          onChange={(e) => upd((p) => ({ ...p, name: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Description *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">What the candidate sees as the brief. Be specific about what they need to accomplish.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]')}
          placeholder="Fix 3 bugs in an async data pipeline that processes streaming JSON events..."
          value={form.description}
          onChange={(e) => upd((p) => ({ ...p, description: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div className="grid grid-cols-3 gap-4">
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Type</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.task_type}
            onChange={(e) => upd((p) => ({ ...p, task_type: e.target.value }))}
            disabled={readOnly}
          >
            <option value="debugging">Debugging</option>
            <option value="ai_engineering">AI Engineering</option>
            <option value="optimization">Optimization</option>
            <option value="build">Build from Scratch</option>
            <option value="refactor">Refactoring</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Difficulty</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.difficulty}
            onChange={(e) => upd((p) => ({ ...p, difficulty: e.target.value }))}
            disabled={readOnly}
          >
            <option value="junior">Junior</option>
            <option value="mid">Mid-Level</option>
            <option value="senior">Senior</option>
            <option value="staff">Staff+</option>
          </select>
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Duration</label>
          <select
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none bg-white')}
            value={form.duration_minutes}
            onChange={(e) => upd((p) => ({ ...p, duration_minutes: parseInt(e.target.value) }))}
            disabled={readOnly}
          >
            <option value={15}>15 min</option>
            <option value={30}>30 min</option>
            <option value={45}>45 min</option>
            <option value={60}>60 min</option>
            <option value={90}>90 min</option>
          </select>
        </div>
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Claude Budget Limit (USD)</label>
        <p className="font-mono text-xs text-gray-500 mb-1">
          Per-candidate Claude spend cap for this task. Leave blank for unlimited.
        </p>
        <input
          type="number"
          step="0.01"
          min="0.01"
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
          placeholder="e.g. 5.00"
          value={form.claude_budget_limit_usd ?? ''}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              upd((p) => ({ ...p, claude_budget_limit_usd: null }));
              return;
            }
            const parsed = Number(raw);
            if (Number.isNaN(parsed)) return;
            upd((p) => ({ ...p, claude_budget_limit_usd: parsed }));
          }}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Primary Role</label>
          <input
            type="text"
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
            placeholder="e.g. backend_engineer"
            value={form.role || ''}
            onChange={(e) => upd((p) => ({ ...p, role: e.target.value }))}
            readOnly={readOnly}
            disabled={readOnly}
          />
        </div>
        <div>
          <label className="block font-mono text-sm mb-1 font-bold">Task Key (optional)</label>
          <input
            type="text"
            className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none')}
            placeholder="e.g. backend_async_pipeline_debug"
            value={form.task_key || ''}
            onChange={(e) => upd((p) => ({ ...p, task_key: e.target.value }))}
            readOnly={readOnly}
            disabled={readOnly}
          />
        </div>
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Scenario / Context</label>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-sm focus:outline-none min-h-[80px]')}
          placeholder="Describe why this task exists and what production context it simulates."
          value={form.scenario || ''}
          onChange={(e) => upd((p) => ({ ...p, scenario: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Starter Code *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">The code the candidate starts with. Include bugs, scaffolding, or an incomplete implementation.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[180px] bg-gray-50 leading-relaxed')}
          placeholder={"# Python starter code\n# Include realistic bugs or incomplete sections\n\ndef process_data(items):\n    ..."}
          value={form.starter_code}
          onChange={(e) => upd((p) => ({ ...p, starter_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
      <div>
        <label className="block font-mono text-sm mb-1 font-bold">Test Suite *</label>
        <p className="font-mono text-xs text-gray-500 mb-1">pytest tests that validate the correct solution. These run automatically when the candidate submits.</p>
        <textarea
          className={inputClass('w-full border-2 border-black px-4 py-3 font-mono text-xs focus:outline-none min-h-[120px] bg-gray-50 leading-relaxed')}
          placeholder={"import pytest\n\ndef test_basic_case():\n    assert process_data([1, 2, 3]) == [2, 4, 6]\n\ndef test_edge_case():\n    assert process_data([]) == []"}
          value={form.test_code}
          onChange={(e) => upd((p) => ({ ...p, test_code: e.target.value }))}
          readOnly={readOnly}
          disabled={readOnly}
        />
      </div>
    </div>
  );
};

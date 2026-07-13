// RequisitionDepartment — the compact hiring-department control in the
// requisition header. A requisition belongs to a HIRING DEPARTMENT (an external
// client like ADCB or an internal team like Engineering); this assigns it, or
// creates a new one inline. The backend entity / field is still
// `client` / `client_id` — only the user-facing label is "hiring department".
//
// It lives INSIDE the header to save vertical space: the old full-width
// economics band (bill rate + estimated margin) was removed — the salary on the
// brief covers compensation, and recruiter-internal margin isn't shown here.
import React, { useEffect, useRef, useState } from 'react';
import { Building2, Check, Plus, X } from 'lucide-react';

import { MotionSpinner } from '../../shared/motion';
import { Select } from '../../shared/ui/TaaliPrimitives';

export function RequisitionDepartment({
  brief,
  clients = [],
  saving = false,
  onAssignClient,
  onCreateClient,
}) {
  const clientId = brief?.client_id ?? '';
  const [addingClient, setAddingClient] = useState(false);
  const [newClientName, setNewClientName] = useState('');
  const newClientRef = useRef(null);

  useEffect(() => {
    if (addingClient) newClientRef.current?.focus();
  }, [addingClient]);

  const submitNewClient = () => {
    const name = newClientName.trim();
    if (!name) return;
    onCreateClient?.(name);
    setNewClientName('');
    setAddingClient(false);
  };

  return (
    <div className="rq-dept" aria-label="Hiring department">
      <Building2 size={13} className="rq-dept-glyph" aria-hidden="true" />
      <span className="rq-dept-label">Hiring dept</span>
      {addingClient ? (
        <div className="rq-dept-new">
          <input
            ref={newClientRef}
            className="rq-dept-input"
            value={newClientName}
            placeholder="New department name"
            disabled={saving}
            onChange={(e) => setNewClientName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); submitNewClient(); }
              if (e.key === 'Escape') { setAddingClient(false); setNewClientName(''); }
            }}
          />
          <button
            type="button"
            className="rq-dept-iconbtn is-primary"
            aria-label="Create hiring department"
            onClick={submitNewClient}
            disabled={saving || !newClientName.trim()}
          >
            <Check size={13} />
          </button>
          <button
            type="button"
            className="rq-dept-iconbtn"
            aria-label="Cancel"
            onClick={() => { setAddingClient(false); setNewClientName(''); }}
            disabled={saving}
          >
            <X size={13} />
          </button>
        </div>
      ) : (
        <>
          <Select
            inline
            aria-label="Assign hiring department"
            className="rq-dept-select"
            value={clientId === null || clientId === undefined ? '' : String(clientId)}
            disabled={saving}
            onChange={(e) => onAssignClient?.(e.target.value)}
          >
            <option value="">— Unassigned —</option>
            {clients.map((c) => (
              <option key={c.id} value={String(c.id)}>{c.name || 'Unnamed department'}</option>
            ))}
          </Select>
          <button
            type="button"
            className="rq-dept-newbtn"
            onClick={() => setAddingClient(true)}
            disabled={saving}
          >
            <Plus size={13} /> New
          </button>
        </>
      )}
      {saving ? <MotionSpinner className="rq-dept-spin" label="Saving" size={15} /> : null}
    </div>
  );
}

export default RequisitionDepartment;

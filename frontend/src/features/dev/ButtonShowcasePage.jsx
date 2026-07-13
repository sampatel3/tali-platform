import React from 'react';
import {
  ArrowRight,
  Check,
  ChevronDown,
  Loader2,
  Mic,
  MoreHorizontal,
  Plus,
  Play,
  Send,
  Settings2,
  Sparkles,
  Square,
  Trash2,
} from 'lucide-react';

import { Button } from '../../shared/ui/TaaliPrimitives';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';
import { VARIANT_G_CSS } from '../marketing/landing_preview/variant_g/variantG.styles';

// Import the production styles that own the feature-local families below.
// The gallery keeps those selectors visible as migration proof while the
// canonical compatibility layer normalises their geometry, semantics, and states.
import '../home/home.css';
import '../home/agentchat/agentchat.css';
import '../chat/chat.css';
import '../requisitions/requisitions.css';
import '../clientintake/clientintake.css';
import '../jobpage/jobpage.css';
import '../clients/clients.css';
import '../../shared/ui/CriteriaEditor.css';
import '../../shared/chat/chat-kit.css';
import './ButtonShowcasePage.css';

const FAMILY_META = [
  { id: 'A', name: 'Shared React primitive' },
  { id: 'B', name: 'Global .btn system' },
  { id: 'C', name: 'Authentication CTA' },
  { id: 'D', name: 'Decision actions' },
  { id: 'E', name: 'Agent chat actions' },
  { id: 'F', name: 'Chat and composer actions' },
  { id: 'G', name: 'Requisition actions' },
  { id: 'I', name: 'Demo actions' },
  { id: 'J', name: 'Candidate report actions' },
  { id: 'K', name: 'Public and form CTAs' },
  { id: 'L', name: 'Compact controls' },
  { id: 'M', name: 'Agent header actions' },
  { id: 'N', name: 'Assessment runtime actions' },
];

const noop = () => {};

const GalleryButton = ({ children, ...props }) => (
  <button type="button" onClick={noop} {...props}>{children}</button>
);

const VariantLabel = ({ children, tone = 'neutral' }) => (
  <span className={`button-lab__variant-label is-${tone}`}>{children}</span>
);

const Sample = ({ label, note, children, className = '' }) => (
  <div className={`button-lab__sample ${className}`}>
    <div className="button-lab__sample-stage">{children}</div>
    <div className="button-lab__sample-caption">
      <span>{label}</span>
      {note ? <small>{note}</small> : null}
    </div>
  </div>
);

const Family = ({
  id,
  title,
  selector,
  usage,
  radius,
  status = 'mapped',
  description,
  children,
}) => (
  <section className="button-lab__family" id={`family-${id}`} data-testid={`button-family-${id}`}>
    <div className="button-lab__family-head">
      <div className="button-lab__family-id" aria-hidden>{id}</div>
      <div className="button-lab__family-copy">
        <div className="button-lab__family-title-row">
          <h2>{title}</h2>
          <VariantLabel tone={status === 'canonical' || status === 'mapped' ? 'purple' : 'neutral'}>
            {status}
          </VariantLabel>
        </div>
        <p>{description}</p>
        <div className="button-lab__meta">
          <code>{selector}</code>
          <span>{usage}</span>
          <span>{radius}</span>
        </div>
      </div>
    </div>
    <div className="button-lab__samples">{children}</div>
  </section>
);

const AuditSummary = () => (
  <section className="button-lab__audit" aria-label="Canonical button system summary">
    <div>
      <strong>7</strong>
      <span>semantic variants</span>
      <small>primary through inverse</small>
    </div>
    <div>
      <strong>4</strong>
      <span>shared sizes</span>
      <small>28px through 48px</small>
    </div>
    <div>
      <strong>14</strong>
      <span>legacy families mapped</span>
      <small>kept below as migration proof</small>
    </div>
    <div className="is-success">
      <strong>1</strong>
      <span>canonical system</span>
      <small>shared component + compatibility layer</small>
    </div>
  </section>
);

const SharedPrimitiveFamily = (props) => (
  <Family
    {...props}
    id="A"
    title="Shared React primitive"
    selector="<Button variant size>"
    usage="7 variants · 4 sizes · shared states"
    radius="10px action radius"
    status="canonical"
    description="The source of truth for every action: one geometry, one state model, and seven semantic treatments that work in light, dark, public, product, and agent contexts."
  >
    <Sample label="Primary" note="main action">
      <Button type="button" variant="primary"><Sparkles size={14} /> Create shortlist</Button>
    </Sample>
    <Sample label="Secondary" note="neutral alternative">
      <Button type="button" variant="secondary">Edit details</Button>
    </Sample>
    <Sample label="Ghost" note="lowest emphasis">
      <Button type="button" variant="ghost">View details</Button>
    </Sample>
    <Sample label="Soft" note="supportive action">
      <Button type="button" variant="soft">Review first</Button>
    </Sample>
    <Sample label="Danger" note="destructive only">
      <Button type="button" variant="danger"><Trash2 size={14} /> Delete role</Button>
    </Sample>
    <Sample label="Agent" note="AI recommendation only">
      <Button type="button" variant="agent"><Sparkles size={14} /> Use recommendation</Button>
    </Sample>
    <Sample label="Inverse" note="dark surfaces" className="is-dark-sample">
      <div className="button-lab__runtime-dark">
        <Button type="button" variant="inverse">Pause agent</Button>
      </div>
    </Sample>
    <Sample label="Sizes" note="xs · sm · md · lg" className="is-wide">
      <div className="button-lab__row">
        <Button type="button" variant="primary" size="xs">XS</Button>
        <Button type="button" variant="primary" size="sm">Small</Button>
        <Button type="button" variant="primary" size="md">Medium</Button>
        <Button type="button" variant="primary" size="lg">Large</Button>
      </div>
    </Sample>
    <Sample label="Loading" note="component-owned state">
      <Button type="button" variant="primary" loading loadingLabel="Saving changes">
        Save changes
      </Button>
    </Sample>
    <Sample label="Disabled" note="shared across variants">
      <Button type="button" variant="secondary" disabled>Can’t continue</Button>
    </Sample>
    <Sample label="Icon only" note="accessible name required">
      <Button type="button" variant="secondary" size="sm" iconOnly aria-label="Settings">
        <Settings2 aria-hidden="true" />
      </Button>
    </Sample>
  </Family>
);

const GlobalButtonFamily = (props) => (
  <Family
    {...props}
    id="B"
    title="Global app / marketing buttons"
    selector=".btn + .btn-*"
    usage="primary · secondary · ghost · danger"
    radius="mapped to 10px · 4 sizes"
    status="mapped"
    description="The largest legacy family now shares canonical geometry and states: both former ink and purple CTAs resolve to primary, outline to secondary, ghost to ghost, and danger to danger."
  >
    <Sample label=".btn-primary" note="→ primary">
      <GalleryButton className="btn btn-primary">Book a demo <ArrowRight className="arrow" size={14} /></GalleryButton>
    </Sample>
    <Sample label=".btn-purple" note="→ primary">
      <GalleryButton className="btn btn-purple"><Plus size={14} /> Add candidate</GalleryButton>
    </Sample>
    <Sample label=".btn-outline" note="→ secondary">
      <GalleryButton className="btn btn-outline">Edit role</GalleryButton>
    </Sample>
    <Sample label=".btn-ghost" note="→ ghost">
      <GalleryButton className="btn btn-ghost">Skip for now</GalleryButton>
    </Sample>
    <Sample label="Compatibility sizes" note="sm · md · lg" className="is-wide">
      <div className="button-lab__row">
        <GalleryButton className="btn btn-outline btn-sm">Small</GalleryButton>
        <GalleryButton className="btn btn-outline">Default</GalleryButton>
        <GalleryButton className="btn btn-outline btn-lg">Large</GalleryButton>
      </div>
    </Sample>
    <Sample label=".btn-xs" note="→ 28px">
      <GalleryButton className="btn btn-purple btn-xs">Extra small</GalleryButton>
    </Sample>
    <Sample label="Disabled" note="shared state">
      <GalleryButton className="btn btn-purple btn-sm" disabled>Processing</GalleryButton>
    </Sample>
    <Sample label=".btn.danger" note="→ danger">
      <GalleryButton className="btn btn-outline btn-sm danger"><Trash2 size={13} /> Remove data</GalleryButton>
    </Sample>
    <Sample label="Landing primary" note="→ primary">
      <div className="lvg button-lab__landing-scope">
        <GalleryButton className="btn btn-primary">Start hiring <ArrowRight className="arw" size={14} /></GalleryButton>
      </div>
    </Sample>
    <Sample label="Landing outline" note="→ secondary">
      <div className="lvg button-lab__landing-scope">
        <GalleryButton className="btn btn-outline">See how it works</GalleryButton>
      </div>
    </Sample>
  </Family>
);

const AuthFamily = (props) => (
  <Family
    {...props}
    id="C"
    title="Authentication CTA"
    selector=".mc-auth-cta"
    usage="primary · secondary · loading"
    radius="mapped to 10px · md"
    status="mapped"
    description="Authentication keeps its full-width layout, while its former ink CTA maps to primary, SSO outline maps to secondary, and loading uses the shared disabled treatment."
  >
    <Sample label="Primary" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="mc-auth-cta">Continue <ArrowRight size={14} /></GalleryButton></div>
    </Sample>
    <Sample label="Outline" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="mc-auth-cta mc-auth-cta-outline">Sign in with SSO</GalleryButton></div>
    </Sample>
    <Sample label="Loading" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="mc-auth-cta" disabled><Loader2 size={14} className="animate-spin" /> Signing in</GalleryButton></div>
    </Sample>
  </Family>
);

const DecisionFamily = (props) => (
  <Family
    {...props}
    id="D"
    title="Decision actions"
    selector=".rq-btn + semantic modifier"
    usage="agent · primary · secondary · ghost · danger"
    radius="mapped to 10px · sm"
    status="mapped"
    description="Decision actions retain their meaning without a private visual system: recommendations map to agent, teaching to primary, alternatives to secondary, deferral to ghost, and confirmed rejection to danger."
  >
    <Sample label="Recommended">
      <GalleryButton className="rq-btn rq-approve"><Sparkles size={13} /> Approve</GalleryButton>
    </Sample>
    <Sample label="Teach">
      <GalleryButton className="rq-btn rq-teach">Teach agent</GalleryButton>
    </Sample>
    <Sample label="Alternative">
      <GalleryButton className="rq-btn rq-override">Override</GalleryButton>
    </Sample>
    <Sample label="Defer">
      <GalleryButton className="rq-btn rq-defer">Decide later</GalleryButton>
    </Sample>
    <Sample label="Ghost">
      <GalleryButton className="rq-btn ghost">Cancel</GalleryButton>
    </Sample>
    <Sample label="Small">
      <GalleryButton className="rq-btn rq-override sm">Re-evaluate</GalleryButton>
    </Sample>
    <Sample label="Disabled">
      <GalleryButton className="rq-btn rq-approve" disabled>Approve</GalleryButton>
    </Sample>
    <Sample label="Agent input option">
      <GalleryButton className="agent-needs-input-option">Use screening score</GalleryButton>
    </Sample>
    <Sample label="Reject arm">
      <GalleryButton className="agent-needs-input-reject">Reject — no CV</GalleryButton>
    </Sample>
    <Sample label="Reject confirm">
      <GalleryButton className="agent-needs-input-reject confirm">Confirm reject</GalleryButton>
    </Sample>
    <Sample label="Input dismiss">
      <GalleryButton className="agent-needs-input-dismiss">Not now</GalleryButton>
    </Sample>
  </Family>
);

const AgentChatFamily = (props) => (
  <Family
    {...props}
    id="E"
    title="Agent chat actions"
    selector=".ac-btn / .ac-chip-toggle"
    usage="primary · soft · ghost · selection controls"
    radius="10px actions · pill choices"
    status="mapped"
    description="Agent-card actions map directly to primary, soft, and ghost. Choice and bulk chips deliberately remain pill-shaped selection controls with pressed state semantics."
  >
    <Sample label="Primary">
      <GalleryButton className="ac-btn ac-btn-primary"><Send size={13} /> Send</GalleryButton>
    </Sample>
    <Sample label="Soft">
      <GalleryButton className="ac-btn ac-btn-soft">Review first</GalleryButton>
    </Sample>
    <Sample label="Ghost">
      <GalleryButton className="ac-btn ac-btn-ghost">Cancel</GalleryButton>
    </Sample>
    <Sample label="Choice chip" note="selection control">
      <GalleryButton className="ac-chip-toggle" aria-pressed="false">Hybrid</GalleryButton>
    </Sample>
    <Sample label="Bulk mode" note="selection control">
      <GalleryButton className="ac-bulk-toggle on" aria-pressed="true"><Check size={11} /> 3 roles</GalleryButton>
    </Sample>
    <Sample label="Disabled">
      <GalleryButton className="ac-btn ac-btn-primary" disabled>Apply</GalleryButton>
    </Sample>
  </Family>
);

const ChatFamily = (props) => (
  <Family
    {...props}
    id="F"
    title="Chat and composer actions"
    selector=".cp-btn-* / .tk-*-btn"
    usage="primary · secondary · ghost · danger"
    radius="mapped to 10px · sm"
    status="mapped"
    description="Modal and composer actions now share one compact grammar: confirm/send is primary, stop is secondary, cancel is ghost, and delete is danger. Dictation remains a pressed toggle."
  >
    <Sample label="Modal primary">
      <GalleryButton className="cp-btn-primary">Confirm</GalleryButton>
    </Sample>
    <Sample label="Modal ghost">
      <GalleryButton className="cp-btn-ghost">Cancel</GalleryButton>
    </Sample>
    <Sample label="Modal danger">
      <GalleryButton className="cp-btn-danger"><Trash2 size={12} /> Delete</GalleryButton>
    </Sample>
    <Sample label="Chat send">
      <GalleryButton className="cp-send-btn"><Send size={12} /> Send</GalleryButton>
    </Sample>
    <Sample label="Shared send">
      <GalleryButton className="tk-send-btn"><Send size={12} /> Send</GalleryButton>
    </Sample>
    <Sample label="Stop">
      <GalleryButton className="tk-stop-btn"><Square size={11} /> Stop</GalleryButton>
    </Sample>
    <Sample label="Voice" note="toggle control">
      <GalleryButton className="tk-mic-btn" aria-pressed="false"><Mic size={12} /> Dictate</GalleryButton>
    </Sample>
    <Sample label="Disabled send">
      <GalleryButton className="tk-send-btn" disabled><Send size={12} /> Send</GalleryButton>
    </Sample>
  </Family>
);

const RequisitionFamily = (props) => (
  <Family
    {...props}
    id="G"
    title="Requisition actions"
    selector=".rq-new-btn / .rq-publish-btn / .rq-btn-sm"
    usage="primary · secondary · soft"
    radius="mapped to 10px · sm/md"
    status="mapped"
    description="Page CTAs and compact editor actions now share the same scale: new/publish/save map to primary, cancel to secondary, and client sharing to soft."
  >
    <Sample label="New requisition" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="rq-new-btn"><Plus size={13} /> New requisition</GalleryButton></div>
    </Sample>
    <Sample label="Publish">
      <GalleryButton className="rq-publish-btn">Publish <ArrowRight size={13} /></GalleryButton>
    </Sample>
    <Sample label="Compact primary">
      <GalleryButton className="rq-btn-sm is-primary">Save changes</GalleryButton>
    </Sample>
    <Sample label="Compact ghost">
      <GalleryButton className="rq-btn-sm is-ghost">Cancel</GalleryButton>
    </Sample>
    <Sample label="Share accent">
      <GalleryButton className="rq-btn-sm is-ghost rq-share-btn">Share with client</GalleryButton>
    </Sample>
    <Sample label="Disabled">
      <GalleryButton className="rq-publish-btn" disabled>Publishing</GalleryButton>
    </Sample>
  </Family>
);

const DemoFamily = (props) => (
  <Family
    {...props}
    id="I"
    title="Demo actions"
    selector=".mc-show-btn"
    usage="primary · secondary · lg CTA"
    radius="canonical 10px · sm/lg"
    status="mapped"
    description="Demo actions preserve their hierarchy while using canonical treatments: neutral maps to secondary, purple maps to primary, and the tall form CTA maps to the shared large size."
  >
    <Sample label="Default">
      <GalleryButton className="mc-show-btn">See evidence</GalleryButton>
    </Sample>
    <Sample label="Primary">
      <GalleryButton className="mc-show-btn primary">Try the workflow <ArrowRight size={13} /></GalleryButton>
    </Sample>
    <Sample label="Tall primary" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="mc-show-btn primary tall">Request a tailored demo</GalleryButton></div>
    </Sample>
    <Sample label="Disabled">
      <GalleryButton className="mc-show-btn primary" disabled>Continue</GalleryButton>
    </Sample>
  </Family>
);

const CandidateReportFamily = (props) => (
  <Family
    {...props}
    id="J"
    title="Candidate report actions"
    selector=".dr-rec-btn / .dr-btn"
    usage="agent · secondary · soft"
    radius="mapped to 10px · sm/lg"
    status="mapped"
    description="The recommendation maps to the static agent treatment at large size; supporting report actions map to secondary or soft and inherit the shared interaction states."
  >
    <Sample label="Recommendation" className="is-wide">
      <div className="dossier-rail button-lab__bounded-control"><GalleryButton className="dr-rec-btn"><Sparkles size={14} /> Progress candidate</GalleryButton></div>
    </Sample>
    <Sample label="Secondary pair" className="is-wide">
      <div className="dossier-rail button-lab__bounded-control"><div className="dr-actions">
        <GalleryButton className="dr-btn">Add note</GalleryButton>
        <GalleryButton className="dr-btn dr-btn-counter">Counter</GalleryButton>
      </div></div>
    </Sample>
    <Sample label="Disabled recommendation" className="is-wide">
      <div className="dossier-rail button-lab__bounded-control"><GalleryButton className="dr-rec-btn" disabled>Progress candidate</GalleryButton></div>
    </Sample>
  </Family>
);

const PublicCtaFamily = (props) => (
  <Family
    {...props}
    id="K"
    title="Public and form CTAs"
    selector=".pjp-* / .ci-* / .cl-* / .tasks-*"
    usage="primary · secondary · link CTA"
    radius="mapped to 10px · sm/md/lg"
    status="mapped"
    description="Public apply, client intake, client creation, bespoke tasks, and inline form actions now resolve to the same primary or secondary recipes; links keep native link semantics."
  >
    <Sample label="Public apply">
      <a href="#family-K" className="pjp-apply-btn" onClick={(event) => event.preventDefault()}>Apply now <ArrowRight size={14} /></a>
    </Sample>
    <Sample label="Client intake" className="is-wide">
      <div className="button-lab__bounded-control"><GalleryButton className="ci-submit-btn">Submit role brief</GalleryButton></div>
    </Sample>
    <Sample label="New client">
      <GalleryButton className="cl-new-btn"><Plus size={13} /> New client</GalleryButton>
    </Sample>
    <Sample label="Bespoke task">
      <GalleryButton className="tasks-bespoke-cta-btn">Request bespoke task</GalleryButton>
    </Sample>
    <Sample label="Inline unsubscribe" note="→ primary md">
      <Button type="button" variant="primary">Unsubscribe</Button>
    </Sample>
  </Family>
);

const CompactFamily = (props) => (
  <Family
    {...props}
    id="L"
    title="Compact, icon, and text controls"
    selector=".ce-btn / .icon-btn / .seg / link buttons"
    usage="xs actions · icon actions · text actions · selections"
    radius="10px actions · pills reserved for choices"
    status="mapped"
    description="Compact actions now use xs geometry, icon actions use square accessible targets, and inline actions use the shared text treatment. Segmented choices remain a separate selection primitive."
  >
    <Sample label="Compact action">
      <GalleryButton className="ce-btn"><Plus size={11} /> Add criterion</GalleryButton>
    </Sample>
    <Sample label="Dashed ghost">
      <GalleryButton className="ce-btn ce-btn--ghost">Show hidden</GalleryButton>
    </Sample>
    <Sample label="Tiny rectangle">
      <GalleryButton className="bg-jobs-panel-btn">View job</GalleryButton>
    </Sample>
    <Sample label="Bordered icon">
      <GalleryButton className="icon-btn" aria-label="Settings"><Settings2 size={15} /></GalleryButton>
    </Sample>
    <Sample label="Compact icon">
      <GalleryButton className="bg-jobs-panel-icon-btn" aria-label="More options"><MoreHorizontal size={16} /></GalleryButton>
    </Sample>
    <Sample label="Text link">
      <GalleryButton className="settings-link-button">Refresh status</GalleryButton>
    </Sample>
    <Sample label="Danger icon" note="destructive action">
      <GalleryButton className="rqt-icon-btn is-danger" aria-label="Remove field"><Trash2 size={14} /></GalleryButton>
    </Sample>
    <Sample label="Segmented" note="selection control" className="is-wide">
      <div className="seg" role="group" aria-label="View choice">
        <GalleryButton className="on" aria-pressed="true">All</GalleryButton>
        <GalleryButton aria-pressed="false">Open</GalleryButton>
        <GalleryButton aria-pressed="false">Closed</GalleryButton>
      </div>
    </Sample>
  </Family>
);

const AgentHeaderFamily = (props) => (
  <Family
    {...props}
    id="M"
    title="Agent header actions"
    selector=".ab-btn / .mc-agent-btn"
    usage="inverse secondary · inverse primary · icon"
    radius="mapped to 10px · sm"
    status="mapped"
    description="Dark agent surfaces use the canonical inverse treatment: translucent secondary actions, a solid high-emphasis action, and square icon actions, all on the shared small geometry."
  >
    <div className="button-lab__dark-stage abar-on">
      <Sample label="Default on dark">
        <GalleryButton className="ab-btn">Pause agent</GalleryButton>
      </Sample>
      <Sample label="Primary on dark">
        <GalleryButton className="ab-btn primary"><Sparkles size={12} /> Review</GalleryButton>
      </Sample>
      <Sample label="Icon on dark">
        <GalleryButton className="ab-btn ic" aria-label="Agent settings"><Settings2 size={14} /></GalleryButton>
      </Sample>
      <Sample label="Legacy solid">
        <GalleryButton className="mc-agent-btn">Configure</GalleryButton>
      </Sample>
      <Sample label="Legacy ghost">
        <GalleryButton className="mc-agent-btn is-ghost">Turn off</GalleryButton>
      </Sample>
    </div>
  </Family>
);

const AssessmentRuntimeFamily = (props) => (
  <Family
    {...props}
    id="N"
    title="Assessment runtime actions"
    selector="former utility-only action combinations"
    usage="primary · secondary · danger · inverse · icon"
    radius="mapped to 10px · 4 sizes"
    status="mapped"
    description="Runtime actions now use the shared component: Run and Submit map to primary, Save and retry to secondary, Dismiss to danger, and compact or inverse actions to their canonical forms. Dock and file selection remain separate controls."
  >
    <Sample label="Run" note="→ primary sm">
      <Button type="button" variant="primary" size="sm">
        <Play size={12} fill="currentColor" /> Run
      </Button>
    </Sample>
    <Sample label="Save outline" note="→ secondary sm">
      <Button type="button" variant="secondary" size="sm">
        Save
      </Button>
    </Sample>
    <Sample label="Retry" note="→ secondary xs">
      <Button type="button" variant="secondary" size="xs">
        Try again
      </Button>
    </Sample>
    <Sample label="Dismiss" note="→ danger sm">
      <Button type="button" variant="danger" size="sm">
        Dismiss
      </Button>
    </Sample>
    <Sample label="Active dock toggle" note="selection control">
      <GalleryButton aria-pressed="true" className="inline-flex items-center gap-1.5 rounded-full border border-[var(--purple)] bg-[var(--purple-soft)] px-3 py-1.5 text-[0.75rem] font-medium text-[var(--purple)]">
        Terminal
      </GalleryButton>
    </Sample>
    <Sample label="Former ink submit" note="→ primary md">
      <Button type="button" variant="primary" size="md">
        Submit <ArrowRight size={13} />
      </Button>
    </Sample>
    <Sample label="Tiny icon" note="→ ghost xs iconOnly">
      <Button type="button" variant="ghost" size="xs" iconOnly aria-label="New runtime file">
        <Plus size={14} />
      </Button>
    </Sample>
    <Sample label="Inverse collapse" note="→ inverse sm" className="is-dark-sample">
      <div className="button-lab__runtime-dark">
        <Button type="button" variant="inverse" size="sm">
          Collapse <ChevronDown size={12} />
        </Button>
      </div>
    </Sample>
  </Family>
);

export const ButtonShowcasePage = () => {
  return (
    <div className="button-lab">
      <style>{VARIANT_G_CSS}</style>
      <header className="button-lab__hero">
        <div className="button-lab__hero-topline">
          <p>Design system · /dev/buttons</p>
          <GlobalThemeToggle appearance="single" />
        </div>
        <div className="button-lab__hero-grid">
          <div>
            <h1>One button system, everywhere.</h1>
            <p className="button-lab__lede">
              Seven semantic variants and four sizes now power actions across the platform.
              The fourteen sections below preserve the old families as proof that every one
              maps into the same geometry, hierarchy, and interaction states.
            </p>
          </div>
          <nav className="button-lab__jump" aria-label="Button family jump links">
            {FAMILY_META.map((family) => (
              <a key={family.id} href={`#family-${family.id}`}>
                <span>{family.id}</span>{family.name}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <main className="button-lab__main">
        <AuditSummary />

        <div className="button-lab__notice" role="note">
          <div><Sparkles size={15} /></div>
          <p>
            <strong>Consolidation is applied.</strong> Family A is the canonical component.
            Compatibility mappings keep legacy selectors aligned while call sites move to it;
            pill-shaped chips, tabs, and segmented choices remain separate selection controls.
          </p>
        </div>

        <SharedPrimitiveFamily />
        <GlobalButtonFamily />
        <AuthFamily />
        <DecisionFamily />
        <AgentChatFamily />
        <ChatFamily />
        <RequisitionFamily />
        <DemoFamily />
        <CandidateReportFamily />
        <PublicCtaFamily />
        <CompactFamily />
        <AgentHeaderFamily />
        <AssessmentRuntimeFamily />

        <footer className="button-lab__footer">
          <ChevronDown size={14} /> End of catalogue · 14 mappings · 1 system
        </footer>
      </main>
    </div>
  );
};

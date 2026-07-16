import { lazy } from 'react';

// Keep route-level code-splitting declarations together. AppShell owns route
// composition; this module owns only the dynamic-import wiring.
export const LandingPage = lazy(() =>
  import('../features/marketing/LandingPage').then((m) => ({ default: m.LandingPage }))
);
export const LoginPage = lazy(() =>
  import('../features/auth/LoginPage').then((m) => ({ default: m.LoginPage }))
);
export const RegisterPage = lazy(() =>
  import('../features/auth/RegisterPage').then((m) => ({ default: m.RegisterPage }))
);
export const ForgotPasswordPage = lazy(() =>
  import('../features/auth/ForgotPasswordPage').then((m) => ({ default: m.ForgotPasswordPage }))
);
export const ResetPasswordPage = lazy(() =>
  import('../features/auth/ResetPasswordPage').then((m) => ({ default: m.ResetPasswordPage }))
);
export const VerifyEmailPage = lazy(() =>
  import('../features/auth/VerifyEmailPage').then((m) => ({ default: m.VerifyEmailPage }))
);
export const AcceptInvitePage = lazy(() =>
  import('../features/auth/AcceptInvitePage').then((m) => ({ default: m.AcceptInvitePage }))
);
export const DashboardNav = lazy(() =>
  import('../shared/layout/Shell').then((m) => ({ default: m.Shell }))
);
export const ConnectWorkableButton = lazy(() =>
  import('../features/integrations/WorkableConnection').then((m) => ({ default: m.ConnectWorkableButton }))
);
export const WorkableCallbackPage = lazy(() =>
  import('../features/integrations/WorkableConnection').then((m) => ({ default: m.WorkableCallbackPage }))
);
export const NotFoundPage = lazy(() =>
  import('../features/marketing/NotFoundPage').then((m) => ({ default: m.NotFoundPage }))
);
export const HomePage = lazy(() =>
  import('../features/home/HomePage').then((m) => ({ default: m.HomePage }))
);
export const PipelineAnalyticsPage = lazy(() =>
  import('../features/analytics/PipelineAnalyticsPage').then((m) => ({ default: m.PipelineAnalyticsPage }))
);
export const AnalyticsPage = lazy(() =>
  import('../features/home/AnalyticsPage').then((m) => ({ default: m.AnalyticsPage }))
);
export const CandidateWelcomePage = lazy(() =>
  import('../features/assessment_runtime/CandidateWelcomePage').then((m) => ({ default: m.CandidateWelcomePage }))
);
export const BackgroundJobsToaster = lazy(() =>
  import('../features/candidates/BackgroundJobsToaster').then((m) => ({ default: m.BackgroundJobsToaster }))
);
export const ToastShowcasePage = lazy(() =>
  import('../features/dev/ToastShowcasePage').then((m) => ({ default: m.ToastShowcasePage }))
);
export const MotionShowcasePage = lazy(() =>
  import('../features/dev/MotionShowcasePage').then((m) => ({ default: m.MotionShowcasePage }))
);
export const ButtonShowcasePage = lazy(() =>
  import('../features/dev/ButtonShowcasePage').then((m) => ({ default: m.ButtonShowcasePage }))
);

export const AssessmentPage = lazy(() => import('../features/assessment_runtime/AssessmentPage'));
export const DemoExperiencePage = lazy(() =>
  import('../features/demo/DemoExperiencePage').then((m) => ({ default: m.DemoExperiencePage }))
);
export const DemoLeadPage = lazy(() =>
  import('../features/marketing/DemoLeadPage').then((m) => ({ default: m.DemoLeadPage }))
);
export const DemoShowcasePage = lazy(() =>
  import('../features/marketing/DemoShowcasePage').then((m) => ({ default: m.DemoShowcasePage }))
);
// Internal, no-auth landing-design preview (/landing-preview?v=a|b|c|d).
export const LandingPreviewPage = lazy(() =>
  import('../features/marketing/landing_preview/LandingPreviewPage').then((m) => ({ default: m.LandingPreviewPage }))
);
export const DeveloperPortalPage = lazy(() =>
  import('../features/developers/DeveloperPortalPage').then((m) => ({ default: m.DeveloperPortalPage }))
);
export const AssessmentsPage = lazy(() =>
  import('../features/assessments/AssessmentsPage').then((m) => ({ default: m.AssessmentsPage }))
);
export const ChatPage = lazy(() =>
  import('../features/chat/ChatPage').then((m) => ({ default: m.ChatPage }))
);
export const ChatShowcaseView = lazy(() =>
  import('../features/chat/ChatShowcaseView').then((m) => ({ default: m.ChatShowcaseView }))
);
export const ChatDesignSystemView = lazy(() =>
  import('../features/chat/ChatDesignSystemView').then((m) => ({ default: m.ChatDesignSystemView }))
);
export const AgentPromptPreviewPage = lazy(() =>
  import('../features/chat/AgentPromptPreviewPage').then((m) => ({ default: m.AgentPromptPreviewPage }))
);
export const HomeShowcaseView = lazy(() =>
  import('../features/home/HomeShowcaseView').then((m) => ({ default: m.HomeShowcaseView }))
);
export const HomeMotionPreview = lazy(() =>
  import('../features/home/HomeMotionPreview').then((m) => ({ default: m.HomeMotionPreview }))
);
export const JobsMotionPreview = lazy(() =>
  import('../features/jobs/JobsMotionPreview').then((m) => ({ default: m.JobsMotionPreview }))
);
export const ReportMotionPreview = lazy(() =>
  import('../features/candidates/ReportMotionPreview').then((m) => ({ default: m.ReportMotionPreview }))
);
export const AnalyticsMotionPreview = lazy(() =>
  import('../features/analytics/AnalyticsMotionPreview').then((m) => ({ default: m.AnalyticsMotionPreview }))
);
export const TopReportPage = lazy(() => import('../features/chat/TopReportPage'));
export const SubmittalPackPage = lazy(() => import('../features/jobs/SubmittalPackPage'));
export const CandidateStandingReportPage = lazy(() =>
  import('../features/candidates/CandidateStandingReportPage').then((m) => ({ default: m.CandidateStandingReportPage }))
);
export const JobsPage = lazy(() =>
  import('../features/jobs/JobsPage').then((m) => ({ default: m.JobsPage }))
);
export const UnsubscribePage = lazy(() => import('../features/outreach/UnsubscribePage'));
export const OutreachThanksPage = lazy(() => import('../features/outreach/OutreachThanksPage'));
export const RequisitionsPage = lazy(() =>
  import('../features/requisitions/RequisitionsPage').then((m) => ({ default: m.RequisitionsPage }))
);
export const PublicJobPage = lazy(() =>
  import('../features/jobpage/PublicJobPage').then((m) => ({ default: m.PublicJobPage }))
);
export const CareersPage = lazy(() =>
  import('../features/jobpage/CareersPage').then((m) => ({ default: m.CareersPage }))
);
export const ClientIntakePage = lazy(() =>
  import('../features/clientintake/ClientIntakePage').then((m) => ({ default: m.ClientIntakePage }))
);
export const JobPipelinePage = lazy(() =>
  import('../features/jobs/JobPipelinePage').then((m) => ({ default: m.JobPipelinePage }))
);
export const TasksPage = lazy(() =>
  import('../features/tasks/TasksPage').then((m) => ({ default: m.TasksPage }))
);
export const TaskPreviewPage = lazy(() =>
  import('../features/tasks/TasksPage').then((m) => ({ default: m.TaskPreviewPage }))
);
export const BespokeTaskRequestPage = lazy(() =>
  import('../features/tasks/BespokeTaskRequestPage').then((m) => ({ default: m.BespokeTaskRequestPage }))
);
export const SettingsPage = lazy(() =>
  import('../features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage }))
);
export const RequisitionTemplatePage = lazy(() =>
  import('../features/settings/RequisitionTemplatePage').then((m) => ({ default: m.RequisitionTemplatePage }))
);
export const AtsAdminPage = lazy(() =>
  import('../features/admin/AtsAdminPage').then((m) => ({ default: m.AtsAdminPage }))
);
export const DecisionPolicyPage = lazy(() => import('../features/decision_policy/DecisionPolicyPage'));
export const TokenGate = lazy(() => import('../features/_dev/TokenGate'));
export const DeckIframe = lazy(() => import('../features/_dev/DeckIframe'));
export const BlogIndexPage = lazy(() => import('../features/blog/BlogIndexPage'));
export const BlogPostPage = lazy(() => import('../features/blog/BlogPostPage'));

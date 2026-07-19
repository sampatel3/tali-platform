import { MemoryRouter } from 'react-router-dom';

const V7_FUTURE_FLAGS = Object.freeze({
  v7_relativeSplatPath: true,
  v7_startTransition: true,
});

export default function TestMemoryRouter({ future, ...props }) {
  return (
    <MemoryRouter
      future={{ ...V7_FUTURE_FLAGS, ...future }}
      {...props}
    />
  );
}

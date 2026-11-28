import { createContext, useContext, useReducer, type Dispatch, type ReactNode } from 'react';
import { initialRunState, runReducer, type RunAction, type RunState } from './runReducer';

interface RunContextValue {
  state: RunState;
  dispatch: Dispatch<RunAction>;
}

const RunContext = createContext<RunContextValue | null>(null);

export function RunProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(runReducer, initialRunState);
  return <RunContext.Provider value={{ state, dispatch }}>{children}</RunContext.Provider>;
}

export function useRunState(): RunContextValue {
  const ctx = useContext(RunContext);
  if (!ctx) throw new Error('useRunState must be used inside RunProvider');
  return ctx;
}

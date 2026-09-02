export interface RoomSpec {
  id: string;
  name: string;
  purpose: string;
  position: { x: number; y: number };
  size: { w: number; h: number };
  color: string;
  agents: AgentSpec[];
  tools: string[];
  skills?: string[];
  max_workers?: number;
  workbenches?: WorkbenchSpec[];
}

export interface WorkbenchSpec {
  id: string;
  name: string;
  job?: string;
  stages?: string[];
  tasks?: string[];
  position?: { x: number; y: number };
  size?: { w: number; h: number };
}

export interface AgentSpec {
  id: string;
  name: string;
  role: string;
  color: string;
}

export interface AgentState {
  id: string;
  name: string;
  color: string;
  home_room: string;
  room_id: string;
  x: number;
  y: number;
  target_x: number;
  target_y: number;
  status: string;
  say: string;
  say_until: number;
  busy?: boolean;
  /** The manifest agent id this worker fills, e.g. "forge" for "forge-2". */
  role?: string;
  /** Extra workers are hired on demand and retired with their lead. */
  ephemeral?: boolean;
  lead_id?: string | null;
  /** Station inside the room this worker is at, if any. */
  workbench?: string | null;
}

export type WireEvent =
  | {
      type: "snapshot";
      rooms: RoomSpec[];
      agents: AgentState[];
      approval_counts?: Record<string, number>;
      t: number;
    }
  | { type: "agent_update"; agent: AgentState }
  | { type: "agent_removed"; agent_id: string }
  | { type: "approvals_updated" }
  | { type: "agent_talk"; from: string; to: string; duration_ms: number; label?: string };

export interface RoomSpec {
  id: string;
  name: string;
  purpose: string;
  position: { x: number; y: number };
  size: { w: number; h: number };
  color: string;
  agents: AgentSpec[];
  tools: string[];
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
  | { type: "approvals_updated" }
  | { type: "agent_talk"; from: string; to: string; duration_ms: number; label?: string };

export async function getRoomState(roomId: string): Promise<any> {
  const r = await fetch(`/rooms/${roomId}/state`);
  if (!r.ok) throw new Error(`state ${roomId}: ${r.status}`);
  return r.json();
}

export async function postRoomAction(
  roomId: string,
  name: string,
  payload: Record<string, unknown> = {},
): Promise<any> {
  const r = await fetch(`/rooms/${roomId}/action`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, payload }),
  });
  if (!r.ok) throw new Error(`action ${roomId}/${name}: ${r.status}`);
  return r.json();
}

export function ActionError({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="rounded-lg border bg-[var(--card)] p-3 text-sm text-[var(--destructive)] shadow-card">{message}</p>
  );
}

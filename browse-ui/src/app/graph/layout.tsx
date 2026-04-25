export default function GraphLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <section className="space-y-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Graph</h1>
        <p className="text-muted-foreground">
          Explore relationship structure across indexed knowledge entries.
        </p>
      </header>
      {children}
    </section>
  );
}

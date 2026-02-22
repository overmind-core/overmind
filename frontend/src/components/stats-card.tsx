import { Card, CardContent, CardHeader } from "@/components/ui/card";

interface StatsCardProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}

export function StatsCard({ title, subtitle, children }: StatsCardProps) {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="border-b border-border px-6 py-4">
        <p className="text-sm font-medium text-muted-foreground">{title}</p>
        {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardContent className="p-6">{children}</CardContent>
    </Card>
  );
}

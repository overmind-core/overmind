import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

interface RawResultAccordionProps {
  result: Record<string, unknown>;
}

export function RawResultAccordion({ result }: RawResultAccordionProps) {
  return (
    <Accordion collapsible type="single">
      <AccordionItem className="border-0" value="raw">
        <AccordionTrigger className="py-2 text-xs text-muted-foreground hover:no-underline">
          Raw Result
        </AccordionTrigger>
        <AccordionContent>
          <pre className="max-h-60 overflow-auto rounded-md border border-border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-all">
            {JSON.stringify(result, null, 2)}
          </pre>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

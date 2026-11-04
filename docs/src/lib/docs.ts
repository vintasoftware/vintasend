import { getCollection, type CollectionEntry } from "astro:content";

// Sidebar order. A page's `section` must be one of these; pages inside a
// section sort by `order`, then by title.
export const SECTIONS = ["Getting started", "Concepts", "Guides", "Ecosystem"] as const;
export type Section = (typeof SECTIONS)[number];

export type DocEntry = CollectionEntry<"docs">;

export async function getOrderedDocs(): Promise<DocEntry[]> {
  const docs = await getCollection("docs");
  return docs.sort((a, b) => {
    const sa = SECTIONS.indexOf(a.data.section);
    const sb = SECTIONS.indexOf(b.data.section);
    if (sa !== sb) return sa - sb;
    if (a.data.order !== b.data.order) return a.data.order - b.data.order;
    return a.data.title.localeCompare(b.data.title);
  });
}

export function groupBySection(docs: DocEntry[]): { section: Section; pages: DocEntry[] }[] {
  return SECTIONS.map((section) => ({
    section,
    pages: docs.filter((d) => d.data.section === section),
  })).filter((g) => g.pages.length > 0);
}

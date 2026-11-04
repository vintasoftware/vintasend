import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";
import { SECTIONS } from "./lib/docs";

// One MDX file per documentation page, under src/content/docs/. The file name
// is the URL slug: `attachments.mdx` is served at /docs/attachments/.
const docs = defineCollection({
  loader: glob({ pattern: "**/*.mdx", base: "./src/content/docs" }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    section: z.enum(SECTIONS),
    order: z.number().int().default(100),
  }),
});

export const collections = { docs };

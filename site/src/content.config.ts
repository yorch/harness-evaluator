import { defineCollection } from 'astro:content';
import { glob, type Loader } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { z } from 'astro/zod';

/**
 * Custom loader that wraps the glob loader to load docs from `../docs/`.
 *
 * - Excludes `index.md` (the custom landing page handles the root route).
 * - Prefixes all slugs with `docs/` so pages are served under `/docs/`.
 * - For entries without a frontmatter `title`, extracts it from the first
 *   H1 heading in the markdown body (e.g. `gateway-proxy.md`).
 */
function harnessbenchDocsLoader(): Loader {
  const inner = glob({
    pattern: ['**/*.md', '!index.md'],
    base: '../docs',
    generateId: ({ entry }) => {
      const withoutExt = entry.replace(/\.[^.]+$/, '');
      const segments = withoutExt.split(/[\\/]/);
      const slug = segments
        .map((s) => s.toLowerCase().replace(/\s+/g, '-'))
        .join('/')
        .replace(/\/index$/, '');
      return slug ? `docs/${slug}` : 'docs';
    },
  });

  return {
    name: 'harnessbench-docs-loader',
    load: async (context) => {
      await inner.load(context);

      // Fix entries with missing titles by extracting from the first H1
      for (const id of context.store.keys()) {
        const entry = context.store.get(id);
        if (!entry) continue;
        if (entry.data.title) continue;

        let title: string | undefined;
        if (entry.body) {
          const match = entry.body.match(/^#\s+(.+)$/m);
          if (match) {
            title = match[1].trim();
          }
        }
        if (!title) {
          // Fallback: derive from slug
          const lastSegment = id.split('/').pop() || '';
          title = lastSegment
            .replace(/-/g, ' ')
            .replace(/\b\w/g, (c) => c.toUpperCase());
        }

        // Re-set the entry with the title added.
        const digest = context.generateDigest({ ...entry.data, title });
        context.store.set({
          id: entry.id,
          data: { ...entry.data, title },
          body: entry.body,
          filePath: entry.filePath,
          rendered: entry.rendered,
          deferredRender: entry.deferredRender,
          assetImports: entry.assetImports,
          digest,
        });
      }
    },
  };
}

export const collections = {
  docs: defineCollection({
    loader: harnessbenchDocsLoader(),
    schema: docsSchema({
      extend: z.object({
        title: z.string().optional(),
      }),
    }),
  }),
};

import type { MetadataRoute } from "next";

import { siteConfig } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: siteConfig.url.toString(),
      lastModified: new Date("2026-08-15"),
      changeFrequency: "weekly",
      priority: 1,
    },
  ];
}

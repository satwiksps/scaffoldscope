import packageMetadata from "@/package.json";

const repository = "https://github.com/satwiksps/scaffoldscope";
const version = packageMetadata.version;
const release = repository + "/releases/tag/v" + version;
const versionedRoot = repository + "/blob/v" + version + "/";

function resolveSiteUrl(): URL {
  const configured =
    process.env.SITE_URL ??
    process.env.VERCEL_PROJECT_PRODUCTION_URL ??
    "https://scaffoldscope.vercel.app";
  const candidate = configured.includes("://")
    ? configured
    : "https://" + configured;
  const url = new URL(candidate);
  const localDevelopment =
    url.hostname === "localhost" ||
    url.hostname === "127.0.0.1" ||
    url.hostname === "::1";

  if (
    url.protocol !== "https:" &&
    !(url.protocol === "http:" && localDevelopment)
  ) {
    throw new Error(
      "SITE_URL must use HTTPS, except for HTTP loopback development",
    );
  }
  if (url.username || url.password) {
    throw new Error("SITE_URL must not contain credentials");
  }

  url.pathname = "/";
  url.search = "";
  url.hash = "";
  return url;
}

export const siteConfig = {
  name: "ScaffoldScope",
  description: "Controlled, paired ablations for coding-agent harnesses.",
  version,
  url: resolveSiteUrl(),
  repository,
  release,
  docs: {
    architecture: versionedRoot + "docs/architecture.md",
    configuration: versionedRoot + "docs/configuration.md",
    experimentDesign: versionedRoot + "docs/experiment-design.md",
    operator: versionedRoot + "docs/operator-guide.md",
    results: versionedRoot + "docs/results-schema.md",
    swebench: versionedRoot + "docs/swebench.md",
  },
  project: {
    changelog: versionedRoot + "CHANGELOG.md",
    contributing: versionedRoot + "CONTRIBUTING.md",
    scope: versionedRoot + "SCOPE.md",
    security: versionedRoot + "SECURITY.md",
  },
} as const;

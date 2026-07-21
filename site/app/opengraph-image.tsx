import { ImageResponse } from "next/og";

import { siteConfig } from "@/lib/site";

export const alt =
  "ScaffoldScope — controlled experiments for coding-agent harnesses";
export const size = {
  width: 1200,
  height: 630,
};
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "#080a0e",
          color: "#f2f5f8",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          justifyContent: "space-between",
          padding: "72px 80px",
          width: "100%",
        }}
      >
        <div
          style={{
            alignItems: "center",
            display: "flex",
            fontSize: 28,
            fontWeight: 650,
            gap: 16,
          }}
        >
          <span
            style={{
              alignItems: "center",
              border: "1px solid #303b49",
              display: "flex",
              height: 48,
              justifyContent: "center",
              width: 48,
            }}
          >
            S<span style={{ color: "#70ddb5" }}>:</span>
          </span>
          ScaffoldScope
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          <div
            style={{
              color: "#70ddb5",
              fontFamily: "monospace",
              fontSize: 20,
              letterSpacing: "0.08em",
              textTransform: "uppercase",
            }}
          >
            Open-source evaluation tooling
          </div>
          <div
            style={{
              fontSize: 70,
              fontWeight: 650,
              letterSpacing: "-0.045em",
              lineHeight: 1.04,
              maxWidth: 1000,
            }}
          >
            Controlled experiments for coding-agent harnesses.
          </div>
        </div>
        <div
          style={{
            borderTop: "1px solid #222b36",
            color: "#a6afba",
            display: "flex",
            fontFamily: "monospace",
            fontSize: 20,
            justifyContent: "space-between",
            paddingTop: 28,
          }}
        >
          <span>paired design · complete traces · verifiable evidence</span>
          <span>v{siteConfig.version} alpha</span>
        </div>
      </div>
    ),
    size,
  );
}

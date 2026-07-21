import { ImageResponse } from "next/og";

export const size = {
  width: 64,
  height: 64,
};

export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          alignItems: "center",
          background: "#0d1117",
          border: "2px solid #303b49",
          color: "#f2f5f8",
          display: "flex",
          fontSize: 30,
          fontWeight: 700,
          height: "100%",
          justifyContent: "center",
          letterSpacing: "-0.08em",
          width: "100%",
        }}
      >
        S<span style={{ color: "#70ddb5" }}>:</span>
      </div>
    ),
    size,
  );
}

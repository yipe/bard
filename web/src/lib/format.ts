export function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`
}

/** h:mm:ss (hours unpadded), e.g. 0:04:05, 16:00:00. */
export function hms(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${h}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
}

/** Local date + time for an epoch-seconds timestamp. */
export function dateTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return "?"
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase()
}

/** Center-crop + resize an image file to a 128×128 JPEG data URL. */
export async function fileToAvatar(file: File, size = 128): Promise<string> {
  const bmp = await createImageBitmap(file)
  const side = Math.min(bmp.width, bmp.height)
  const canvas = document.createElement("canvas")
  canvas.width = canvas.height = size
  const ctx = canvas.getContext("2d")
  if (!ctx) throw new Error("canvas unsupported")
  ctx.drawImage(bmp, (bmp.width - side) / 2, (bmp.height - side) / 2, side, side, 0, 0, size, size)
  bmp.close()
  return canvas.toDataURL("image/jpeg", 0.85)
}

"use strict";

const button = document.getElementById("record");
const status = document.getElementById("status");
const endpoint = "/fantasy-football/api/test-write";

async function marker(record) {
  button.disabled = true;
  status.textContent = record ? "Recording synthetic marker…" : "Checking your marker…";
  try {
    const response = await fetch(endpoint, {
      method: record ? "POST" : "GET",
      credentials: "same-origin",
      mode: "same-origin",
      // Override the page's no-referrer policy so this POST retains its Origin.
      referrerPolicy: "same-origin",
      redirect: "error",
      cache: "no-store",
      headers: record ? {"Content-Type": "application/json", "X-FFOPT-Write": "record-v1"} : {},
      ...(record ? {body: JSON.stringify({action: "record"})} : {}),
    });
    if (!response.ok) {
      status.textContent = response.status === 401 || response.status === 403
        ? "Sign in with an approved account before recording."
        : "The write probe is unavailable. Nothing is reported as successful.";
      return;
    }
    const result = await response.json();
    if (result.recorded === false && result.marker === null) {
      status.textContent = "No synthetic marker recorded yet.";
    } else if (result.recorded === true && result.marker.value === "synthetic-write-v1"
        && typeof result.marker.created_at === "string") {
      status.textContent = `Synthetic marker recorded at ${result.marker.created_at}. Repeats keep this timestamp.`;
    } else {
      throw new Error("Unexpected probe response");
    }
  } catch {
    status.textContent = "Could not confirm your marker. Check your connection and retry; repeats are safe.";
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", () => marker(true));
marker(false);

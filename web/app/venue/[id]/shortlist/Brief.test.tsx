// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BriefOut } from "@/src/lib/api-client";
import { fakeApi } from "../../new/test-utils";
import { Brief, PencilToggle, resetBriefCache } from "./brief";
import fixture from "./fixtures/brief.json";
import soundcheck from "./fixtures/soundcheck.json";
import { HIGHLIGHT_MS, SoundcheckButton, resetSoundcheckCache } from "./soundcheck";

const data = fixture as BriefOut;
const entityId = "urn:entity:synthetic:artist:11";
const panelUrl = `/api/runs/run_fixture/candidates/${encodeURIComponent(entityId)}`;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetSoundcheckCache();
  resetBriefCache();
});

const section = (name: string) => within(screen.getByRole("heading", { name }).closest("section")!);
const sourceOf = (name: string) => section(name).getAllByTestId("source")[0].dataset.source;

describe("Brief tab", () => {
  it("renders every field of the brief with its source label", () => {
    render(<Brief data={data} linkable={() => true} onEvidence={() => {}} />);

    const reasons = screen.getAllByTestId("fit-reason");
    expect(reasons.map((r) => r.querySelector("p")?.textContent)).toEqual([
      "Qloo ranks Synthetic Headliner among this crowd's strongest affinities.",
      "The room's past sell-outs share its audience.",
      "Synthetic Opener pulls from the same crowd at a smaller size.",
    ]);
    // Each bullet names the evidence it cites, with that item's own label.
    expect(reasons.map((r) => within(r).getByTestId("source").dataset.source)).toEqual([
      "qloo",
      "qloo",
      "qloo",
    ]);
    expect(within(reasons[1]).getByRole("button").textContent).toBe(
      "Evidence: Qloo value 0.81 behind past_fit",
    );

    expect(section("Night format").getByText(/Two-act show/)).toBeTruthy();
    expect(section("Support act").getByText("Synthetic Opener")).toBeTruthy();
    expect(section("Support act").getByText(/64%/)).toBeTruthy();
    expect(
      within(screen.getByRole("list", { name: "Partners" })).getAllByRole("listitem"),
    ).toHaveLength(2);
    expect(screen.getByTestId("email-subject").textContent).toBe(
      "Synthetic Headliner at Synthetic Room",
    );
    expect(screen.getByTestId("email-body").textContent).toContain("would like to host");

    expect(
      ["Why it fits", "Night format", "Support act", "Partners", "Outreach email"].map(sourceOf),
    ).toEqual(["draw", "draw", "qloo", "qloo", "draw"]);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("links only the refs the trail holds, and hands the ref to onEvidence", async () => {
    const onEvidence = vi.fn();
    const inTrail = new Set(Object.keys(soundcheck.trail.source_labels));
    render(<Brief data={data} linkable={(ref) => inTrail.has(ref)} onEvidence={onEvidence} />);

    // The support call is not in the act's trail: plain text, not a dead link.
    expect(screen.getAllByTestId("evidence-link")).toHaveLength(2);
    expect(screen.getByTestId("evidence-text").textContent).toContain("support pool");

    await userEvent.setup().click(screen.getAllByTestId("evidence-link")[0]);
    expect(onEvidence).toHaveBeenCalledWith("call:00000000-0000-4000-8000-0000000000a1");
  });

  it("copies the subject and the body", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    render(<Brief data={data} />);

    await user.click(screen.getByRole("button", { name: "Copy subject" }));
    expect(writeText).toHaveBeenLastCalledWith("Synthetic Headliner at Synthetic Room");
    await user.click(screen.getByRole("button", { name: "Copy body" }));
    expect(writeText).toHaveBeenLastCalledWith(data.brief.outreach_email.body);
    expect(await navigator.clipboard.readText()).toBe(data.brief.outreach_email.body);
    expect(screen.getAllByText("Copied").length).toBeGreaterThan(0);
  });

  it("says when the brief is Outdraw's template, and when there is no support act", () => {
    render(
      <Brief
        data={{
          ...data,
          error: "unusable",
          brief: { ...data.brief, support_act: null, partners: [] },
          support: { pool: 0, note: "No smaller act on this shortlist." },
        }}
      />,
    );
    expect(screen.getByRole("note").textContent).toContain("Outdraw's template");
    expect(section("Support act").getByText("No smaller act on this shortlist.")).toBeTruthy();
    expect(section("Partners").getByText("No partner places in the crowd profile.")).toBeTruthy();
    // No onEvidence: every ref is plain text.
    expect(screen.queryAllByTestId("evidence-link")).toHaveLength(0);
  });
});

describe("Soundcheck panel tabs and pencil hold", () => {
  function open() {
    render(<SoundcheckButton runId="run_fixture" entityId={entityId} name="Synthetic Headliner" />);
    return userEvent.setup();
  }

  it("fetches the brief only when its tab opens, and links back to the trail", async () => {
    const calls = fakeApi([
      [/\/soundcheck$/, Response.json(soundcheck)],
      [/\/brief$/, Response.json(fixture)],
    ]);
    const user = open();
    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    await screen.findByRole("heading", { name: "Why this act" });
    expect(calls.map((c) => c.url)).toEqual([`${panelUrl}/soundcheck`]);

    const briefTab = screen.getByRole("tab", { name: "Brief" });
    expect(screen.getByRole("tab", { name: "Evidence" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    await user.click(briefTab);
    expect(briefTab.getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByRole("heading", { name: "Outreach email" })).toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual([`${panelUrl}/soundcheck`, `${panelUrl}/brief`]);

    // An evidence link opens the Evidence tab on the cited trail item.
    await user.click(screen.getAllByTestId("evidence-link")[1]);
    const target = document.activeElement as HTMLElement;
    expect(target.dataset.ref).toBe("affinity:past_fit");
    expect(target.textContent).toContain("Past fit 0.81");
    expect(target.dataset.highlight).toBe("");
    expect(screen.getByRole("tab", { name: "Evidence" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    // The highlight is a flash, not a state: it clears on its own.
    await waitFor(() => expect(target.dataset.highlight).toBeUndefined(), {
      timeout: HIGHLIGHT_MS + 500,
    });

    // Back to the brief: the cached copy, no second fetch.
    await user.click(screen.getByRole("tab", { name: "Brief" }));
    expect(screen.getByRole("heading", { name: "Outreach email" })).toBeTruthy();
    expect(calls).toHaveLength(2);
  });

  it("shows the reason and retries when the brief fails", async () => {
    fakeApi([
      [/\/soundcheck$/, Response.json(soundcheck)],
      [/\/brief$/, Response.json({ reason: "budget" }, { status: 429 })],
      [/\/brief$/, Response.json(fixture)],
    ]);
    const user = open();
    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    await user.click(await screen.findByRole("tab", { name: "Brief" }));
    expect((await screen.findByRole("alert")).textContent).toContain("today's Qloo budget");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Why it fits" })).toBeTruthy();
  });

  it("opens with the stored pencil hold and PATCHes the new one", async () => {
    const calls = fakeApi([
      [/\/soundcheck$/, Response.json({ ...soundcheck, pencilled: true })],
      [
        /candidates\/[^/]+$/,
        Response.json({
          run_id: "run_fixture",
          entity_id: entityId,
          pencilled: false,
        }),
      ],
    ]);
    const user = open();
    const trigger = screen.getByRole("button", { name: /Soundcheck/ });
    await user.click(trigger);

    const pencil = await screen.findByRole("switch", { name: "Pencil hold" });
    expect(pencil.getAttribute("aria-checked")).toBe("true");
    await user.click(pencil);
    expect(calls[1]).toEqual({
      method: "PATCH",
      url: panelUrl,
      body: { pencilled: false },
    });
    expect(screen.getByRole("switch", { name: "Pencil hold" }).getAttribute("aria-checked")).toBe(
      "false",
    );

    // Reopening in the same page keeps the stored value without refetching.
    await user.keyboard("{Escape}");
    await user.click(trigger);
    expect(screen.getByRole("switch", { name: "Pencil hold" }).getAttribute("aria-checked")).toBe(
      "false",
    );
    expect(calls).toHaveLength(2);
  });

  it("keeps focus and ignores clicks while a save is in flight, and follows a new initial", async () => {
    let answer!: (r: Response) => void;
    const calls = fakeApi([
      [/\/soundcheck$/, Response.json(soundcheck)],
      [/candidates\/[^/]+$/, () => new Promise<Response>((r) => (answer = r))],
    ]);
    const user = open();
    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    const pencil = await screen.findByRole("switch", { name: "Pencil hold" });

    await user.click(pencil);
    expect(pencil.getAttribute("aria-busy")).toBe("true");
    expect(pencil.hasAttribute("disabled")).toBe(false);
    expect(document.activeElement).toBe(pencil);
    // A second click while saving sends nothing.
    await user.click(pencil);
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1);
    expect(pencil.getAttribute("aria-checked")).toBe("true");

    answer(
      Response.json({
        run_id: "run_fixture",
        entity_id: entityId,
        pencilled: true,
      }),
    );
    await waitFor(() => expect(pencil.getAttribute("aria-busy")).toBe("false"));
    expect(pencil.getAttribute("aria-checked")).toBe("true");
  });

  it("follows a new initial value", () => {
    const { rerender } = render(
      <PencilToggle runId="run_fixture" entityId={entityId} initial={false} />,
    );
    const pencil = screen.getByRole("switch", { name: "Pencil hold" });
    expect(pencil.getAttribute("aria-checked")).toBe("false");
    rerender(<PencilToggle runId="run_fixture" entityId={entityId} initial={true} />);
    expect(pencil.getAttribute("aria-checked")).toBe("true");
  });

  it("flips the pencil back with the reason when the PATCH fails", async () => {
    fakeApi([
      [/\/soundcheck$/, Response.json(soundcheck)],
      [/candidates\/[^/]+$/, Response.json({ reason: "unavailable" }, { status: 503 })],
    ]);
    const user = open();
    await user.click(screen.getByRole("button", { name: /Soundcheck/ }));
    const pencil = await screen.findByRole("switch", { name: "Pencil hold" });

    await user.click(pencil);
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach Outdraw");
    expect(pencil.getAttribute("aria-checked")).toBe("false");
  });
});

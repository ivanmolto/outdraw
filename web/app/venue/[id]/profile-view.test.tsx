// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import fixture from "./fixtures/venue.json";
import Loading from "./loading";
import { ProfileView, type Profile } from "./profile-view";

const profile = fixture.profile as Profile;

afterEach(cleanup);

function setup(p: Profile = profile) {
  render(<ProfileView venueId="ven_fixture" venueName="Synthetic Room" profile={p} />);
}

const card = (name: string) => screen.getByRole("region", { name });

describe("crowd profile", () => {
  it("renders a card per list from a fixture profile, each labelled as Qloo data", () => {
    setup();

    expect(screen.getByRole("heading", { level: 1, name: "Synthetic Room" })).toBeTruthy();
    for (const name of ["Genres", "Related artists", "Places", "Films", "Brands", "Demographics"]) {
      expect(within(card(name)).getByText("Qloo data")).toBeTruthy();
    }
    expect(within(card("Genres")).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Synthetic Wave0.91",
      "Fixture Rock0.74",
    ]);
    expect(within(card("Related artists")).getByText("Synthetic Artist Four")).toBeTruthy();
    expect(within(card("Films")).getByText(/Qloo returned nothing here/)).toBeTruthy();
  });

  it("shows affinities as bars and demographics by group, without bookkeeping keys", () => {
    setup();

    const [bar] = within(card("Genres")).getAllByTestId("bar");
    expect(bar.getAttribute("aria-hidden")).toBe("true");
    expect((bar.firstElementChild as HTMLElement).style.width).toBe("91%");
    const demo = within(card("Demographics"));
    expect(demo.getByText("24 and younger")).toBeTruthy();
    expect(demo.getByText("female")).toBeTruthy();
    expect(demo.queryByText(/entities|urn:entity/)).toBeNull();
  });

  it("hides the demographics card when the profile has none", () => {
    const rest: Profile = { ...profile };
    delete rest.demographics;
    setup(rest);

    expect(screen.queryByRole("region", { name: "Demographics" })).toBeNull();
    expect(screen.getAllByRole("region")).toHaveLength(5);
  });

  it("hides the demographics card when it has no age or gender data", () => {
    setup({ ...profile, demographics: { entities: ["urn:entity:synthetic:artist:1"] } });

    expect(screen.queryByRole("region", { name: "Demographics" })).toBeNull();
  });

  it("labels partial data as Outdraw's reading and lists the notes", () => {
    setup();

    const banner = within(screen.getByRole("complementary", { name: "Data quality" }));
    expect(banner.getByText("Some of this profile is partial.")).toBeTruthy();
    expect(banner.getByText("Outdraw")).toBeTruthy();
    expect(banner.getByText(/films: Qloo returned no results/)).toBeTruthy();
  });

  it("shows no banner for a clean profile", () => {
    setup({ ...profile, data_quality: "ok", notes: [] });

    expect(screen.queryByRole("complementary", { name: "Data quality" })).toBeNull();
  });

  it("links to the shortlist", () => {
    setup();

    const link = screen.getByRole("link", { name: "Build shortlist" });
    expect(link.getAttribute("href")).toBe("/venue/ven_fixture/shortlist");
  });
});

describe("loading state", () => {
  it("renders a skeleton with the profile's card grid", () => {
    render(<Loading />);

    expect(screen.getByRole("status", { name: "Loading crowd profile" })).toBeTruthy();
    expect(screen.getAllByTestId("skeleton-card")).toHaveLength(6);
  });
});

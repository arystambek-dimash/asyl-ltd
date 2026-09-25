import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Client } from "@/lib/types";
import { UnassignedClients } from "./unassigned-clients";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({
  api: { get, post },
  apiError: (e: Error) => e.message,
  isCanceledRequest: () => false,
}));
vi.mock("@/lib/toast", () => ({ showSuccess: vi.fn() }));

const waiting = { id: 7, name: "Махашов Сейдекерим", phone: "+7 (702) 069-95-18", company_name: "" } as Client;
const ownDepartment = { id: 3, code: "mill", name: "Мельница", color: "#315FD5" };
let unassigned: Client[] = [];

beforeEach(() => {
  unassigned = [waiting];
  get.mockReset().mockImplementation(async () => ({
    data: { count: unassigned.length, next: null, previous: null, results: unassigned },
  }));
  post.mockReset().mockImplementation(async () => {
    unassigned = [];
    return { data: {} };
  });
});

it("показывает, сколько клиентов ждут отдела, и закрепляет за своим отделом только после «Да»", async () => {
  const user = userEvent.setup();
  const onAssigned = vi.fn();
  render(<UnassignedClients ownDepartment={ownDepartment} departments={[]} onAssigned={onAssigned} />);

  await user.click(await screen.findByRole("button", { name: /1 клиент ждёт отдела/ }));
  const dialog = await screen.findByRole("dialog");
  await user.click(await within(dialog).findByRole("button", { name: "В мой отдел" }));

  const question = within(dialog).getByRole("alertdialog", { name: `Закрепить ${waiting.name}` });
  expect(question).toHaveTextContent("Закрепить за отделом «Мельница»?");
  await user.click(within(question).getByRole("button", { name: "Нет" }));
  expect(post).not.toHaveBeenCalled();

  await user.click(within(dialog).getByRole("button", { name: "В мой отдел" }));
  await user.click(within(dialog).getByRole("button", { name: "Да" }));

  await waitFor(() => expect(post).toHaveBeenCalledWith("/clients/7/assign-department/", { department: 3 }));
  expect(onAssigned).toHaveBeenCalled();
  expect(await within(dialog).findByText("Все клиенты распределены.")).toBeInTheDocument();
});

it("ничего не показывает, когда все клиенты распределены", async () => {
  unassigned = [];
  const { container } = render(
    <UnassignedClients ownDepartment={ownDepartment} departments={[]} onAssigned={vi.fn()} />,
  );

  await waitFor(() => expect(get).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});

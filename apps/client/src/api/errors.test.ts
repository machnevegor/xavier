import { describe, expect, it } from "vitest";
import { ApiError, apiError, explain, networkError } from "@/api/errors.ts";

describe("apiError", () => {
  it("reads the message and validation details", () => {
    const error = apiError(new Response(null, { status: 422 }), {
      details: [
        {
          loc: ["body", "data", "name"],
          msg: "Field required",
          type: "missing",
        },
      ],
      message: "Validation failed",
      success: false,
    });
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.message).toBe("Validation failed");
    expect(error.details).toEqual([
      { loc: ["body", "data", "name"], msg: "Field required", type: "missing" },
    ]);
  });

  it("ignores a body that is not the error shape", () => {
    const error = apiError(
      new Response(null, { status: 500, statusText: "Oops" }),
      null,
    );
    expect(error.message).toBe("Oops");
    expect(error.details).toBeNull();
  });
});

describe("explain", () => {
  it("maps statuses to the standing copy", () => {
    expect(explain(new ApiError(401, "Authentication required")).title).toBe(
      "Not signed in to Xavier",
    );
    expect(explain(new ApiError(404, "Not found")).hint).toBe(
      "It may have been deleted.",
    );
    expect(explain(new ApiError(409, "Conflict")).title).toBe(
      "That is not possible right now",
    );
    expect(explain(new ApiError(413, "Payload too large")).title).toBe(
      "File too large",
    );
    expect(explain(new ApiError(422, "a message needs text"))).toEqual({
      hint: "a message needs text",
      title: "Check your request",
    });
    expect(explain(new ApiError(503, "Service unavailable")).title).toBe(
      "Xavier is temporarily unavailable",
    );
  });

  it("keeps the specific copy when the message names the cause", () => {
    expect(explain(new ApiError(409, "Chat budget exhausted")).title).toBe(
      "This chat has reached its budget",
    );
    expect(explain(new ApiError(409, "Computer is busy")).title).toBe(
      "The Computer is busy",
    );
    expect(explain(new ApiError(409, "Workspace is hibernated")).hint).toBe(
      "Wake the workspace to view its files and screen. Files are restored from the last checkpoint.",
    );
    expect(explain(new ApiError(422, "wrong current password"))).toEqual({
      hint: "Enter your current password and try again.",
      title: "Current password is incorrect",
    });
  });

  it("describes a network failure", () => {
    expect(explain(networkError(new Error("failed"))).title).toBe(
      "Cannot reach Xavier",
    );
  });
});

import { Router, type IRouter } from "express";
import {
  CreateBusinessBody,
  CreateBusinessResponse,
  GetBusinessParams,
  GetBusinessResponse,
  ListBusinessesResponse,
  UpdateBusinessBody,
  UpdateBusinessParams,
  UpdateBusinessResponse,
} from "@workspace/api-zod";
import {
  createBusiness,
  getBusiness,
  listBusinesses,
  updateBusiness,
} from "../lib/business-store";

const router: IRouter = Router();

router.get("/businesses", (_req, res) => {
  res.json(ListBusinessesResponse.parse(listBusinesses()));
});

router.post("/businesses", (req, res) => {
  const parsed = CreateBusinessBody.safeParse(req.body);
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  res.status(201).json(CreateBusinessResponse.parse(createBusiness(parsed.data)));
});

router.get("/businesses/:id", (req, res) => {
  const params = GetBusinessParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }
  const business = getBusiness(params.data.id);
  if (!business) {
    res.status(404).json({ error: "Business not found" });
    return;
  }
  res.json(GetBusinessResponse.parse(business));
});

router.patch("/businesses/:id", (req, res) => {
  const params = UpdateBusinessParams.safeParse(req.params);
  const parsed = UpdateBusinessBody.safeParse(req.body);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }
  if (!parsed.success) {
    res.status(400).json({ error: parsed.error.message });
    return;
  }
  const business = updateBusiness(params.data.id, parsed.data);
  if (!business) {
    res.status(404).json({ error: "Business not found" });
    return;
  }
  res.json(UpdateBusinessResponse.parse(business));
});

export default router;
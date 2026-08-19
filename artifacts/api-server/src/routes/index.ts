import { Router, type IRouter } from "express";
import healthRouter from "./health";
import businessesRouter from "./businesses";

const router: IRouter = Router();

router.use(healthRouter);
router.use(businessesRouter);

export default router;

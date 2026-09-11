import { Link } from "react-router-dom";
import { EmptyState } from "@/components/primitives";

export function NotFoundPage() {
  return (
    <div className="page">
      <EmptyState
        title="That page does not exist"
        action={<Link to="/incidents" className="btn btn--primary">Go to the incident queue</Link>}
      >
        Check the address, or start from the incident queue.
      </EmptyState>
    </div>
  );
}

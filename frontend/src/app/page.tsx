import { EvidenceChain } from "@/components/landing/EvidenceChain";
import { Hero } from "@/components/landing/Hero";
import { Nav } from "@/components/landing/Nav";
import {
  Features,
  FinalCta,
  Footer,
  Numbers,
  QuestionMarquee,
  Steps,
} from "@/components/landing/Sections";
import { Strata } from "@/components/landing/Strata";

export default function Landing() {
  return (
    <div className="min-h-full overflow-x-clip bg-ink-950">
      <a
        href="#main"
        className="sr-only z-50 rounded bg-lamp px-3 py-2 text-ink-950 focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <Nav />
      <main id="main">
        <Hero />
        <QuestionMarquee />
        <Strata />
        <EvidenceChain />
        <Numbers />
        <Features />
        <Steps />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}

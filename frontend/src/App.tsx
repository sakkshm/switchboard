import { useRef, useState } from "react"
import { ArrowDown, ArrowRight, ArrowUpRight, Check, Copy, Files, FolderGit2, Code2, Command, Terminal } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

const repository = "https://github.com/sakkshm/switchboard"
const commands = {
  unix: 'python3 -m venv .venv\nsource .venv/bin/activate\npip install -e ".[dev]"\ncp .env.example .env\ndocker compose up -d --wait\nswitchboard add mcp filesystem -- npx -y @modelcontextprotocol/server-filesystem "$PWD"\nswitchboard index\nswitchboard serve --http 127.0.0.1:8765',
  windows: 'python -m venv .venv\n.\\.venv\\Scripts\\Activate.ps1\npip install -e ".[dev]"\nCopy-Item .env.example .env\ndocker compose up -d --wait\nswitchboard add mcp filesystem -- npx.cmd -y @modelcontextprotocol/server-filesystem (Get-Location).Path\nswitchboard index\nswitchboard serve --http 127.0.0.1:8765',
}
const workflow = [
  { name: "index", label: "Connect your tools", description: "Register your upstream MCP servers. Switchboard discovers their tools and stores declared input and output dependencies as a graph in HydraDB.", input: "Your registered MCP servers", output: "A graph of tools and dependencies", command: "switchboard index" },
  { name: "find", label: "Find the path", description: "Your agent sends a task. Switchboard matches it to relevant tools, traverses the graph, and checks the dependency order before returning a plan.", input: "A task and its available inputs", output: "A plan ID and ordered steps", command: 'find(task, inputs)' },
  { name: "run", label: "Run the plan", description: "The gateway calls the upstream tools in order, passing outputs into the next step. Execution results update the route weights for future plans.", input: "A plan ID and required inputs", output: "Results, or the step that failed", command: 'run(plan_id, inputs)' },
]

function Mark() {
  return <span className="mark" aria-hidden="true"><img src={`${import.meta.env.BASE_URL}switchboard-logo.png`} alt="" width="602" height="674"/></span>
}

function SetupDialog() {
  const [platform, setPlatform] = useState<keyof typeof commands>("unix")
  const [feedback, setFeedback] = useState<"idle" | "copied" | "failed">("idle")
  const copyRequest = useRef(0)
  async function copy() {
    const request = ++copyRequest.current
    try {
      await navigator.clipboard.writeText(commands[platform])
      if (request === copyRequest.current) setFeedback("copied")
    } catch {
      if (request === copyRequest.current) setFeedback("failed")
    }
  }
  return <Dialog>
    <DialogTrigger asChild><Button className="primary-action">Run locally <ArrowUpRight size={16}/></Button></DialogTrigger>
    <DialogContent className="setup-dialog">
      <DialogHeader>
        <span className="kicker">01 / GET CONNECTED</span>
        <DialogTitle>One connection to get started.</DialogTitle>
        <DialogDescription>Run from your Switchboard checkout. Requires Python 3.11+, Node.js, Git and Docker. This registers the filesystem MCP server with access to read and write files in this checkout.</DialogDescription>
      </DialogHeader>
      <Tabs value={platform} onValueChange={value => { copyRequest.current++; setPlatform(value as keyof typeof commands); setFeedback("idle") }}>
        <TabsList className="platform-tabs"><TabsTrigger value="unix">macOS / Linux</TabsTrigger><TabsTrigger value="windows">Windows</TabsTrigger></TabsList>
        {(["unix", "windows"] as const).map(value => <TabsContent value={value} key={value}><pre className="command-block"><code>{commands[value]}</code></pre></TabsContent>)}
      </Tabs>
      <div className="setup-bottom">
        <p>Once HydraDB is ready, index the tools and leave the gateway running. Add <code>http://127.0.0.1:8765/mcp</code> as an HTTP MCP server in your agent client.</p>
        <Button variant="outline" onClick={copy}>{feedback === "copied" ? <Check size={14}/> : <Copy size={14}/>} {feedback === "copied" ? "Copied" : "Copy commands"}</Button>
      </div>
      <p className="setup-note">For multi-step routes, declare tool dependencies with <code>switchboard annotate</code> or use <code>annotate --infer</code>, then index again. The repository guide covers configuration and real-server examples.</p>
      <p className="copy-status" role="status">{feedback === "failed" ? "Clipboard unavailable. Select and copy the commands above." : feedback === "copied" ? "Commands copied to clipboard." : ""}</p>
      <a className="text-link" href={`${repository}#install`} target="_blank" rel="noreferrer">Full installation guide <ArrowUpRight size={14}/></a>
    </DialogContent>
  </Dialog>
}

function WorkflowDialog() {
  const [step, setStep] = useState(0)
  const selected = workflow[step]
  return <Dialog>
    <DialogTrigger asChild><Button variant="outline" className="secondary-action">Explore the workflow <ArrowRight size={16}/></Button></DialogTrigger>
    <DialogContent className="workflow-dialog">
      <DialogHeader><span className="kicker">THE PATH FROM TASK TO ACTION</span><DialogTitle>Three steps. Two agent tools.</DialogTitle><DialogDescription>Index at setup. Find and run from your agent. This guide explains the architecture; it does not execute tools.</DialogDescription></DialogHeader>
      <div className="workflow-explorer">
        <div className="workflow-steps" aria-label="Explore the gateway workflow">{workflow.map((item, index) => <button className={`workflow-step ${step === index ? "selected" : ""}`} key={item.name} onClick={() => setStep(index)} aria-pressed={step === index}><span>0{index+1}</span><code>{item.name}</code><ArrowUpRight size={16}/></button>)}</div>
        <div className="step-detail"><h3>{selected.label}</h3><p>{selected.description}</p><div className="binding"><span>INPUT</span><p>{selected.input}</p><ArrowDown size={16}/><span>OUTPUT</span><p>{selected.output}</p></div><code className="step-command">{selected.command}</code></div>
      </div>
      <p className="setup-note">Available routes depend on the servers you register and the dependency types declared for their tools.</p>
    </DialogContent>
  </Dialog>
}

function RoutingMap() {
  const [source, setSource] = useState(0)
  const [client, setClient] = useState(0)
  const [trace, setTrace] = useState(0)
  const sources = [
    { label: "Filesystem", icon: Files },
    { label: "Git", icon: FolderGit2 },
  ]
  const clients = [
    { label: "OpenCode", icon: Terminal },
    { label: "Cursor", icon: Code2 },
    { label: "Claude Desktop", icon: Command },
  ]
  const incoming = (index: number) => `M 216 ${index === 0 ? 92 : 198} C 306 ${index === 0 ? 92 : 198}, 333 145, 411 145`
  const outgoing = (index: number) => `M 589 145 C 670 145, 681 ${[42, 145, 248][index]}, 784 ${[42, 145, 248][index]}`
  return <div className="architecture" aria-label="Switchboard architecture" role="group">
    <div className="architecture-top"><span className="kicker">CHOOSE A SERVER. CHOOSE AN AGENT.</span><button className="trace-button" onClick={() => setTrace(value => value + 1)}>Trace route <ArrowRight size={14}/></button></div>
    <div className="routing-labels"><span>YOUR MCP SERVERS</span><span>ONE GATEWAY</span><span>YOUR AGENT</span></div>
    <div className="routing-map">
      <svg key={`${source}-${client}-${trace}`} className="routing-wires" viewBox="0 0 1000 290" preserveAspectRatio="none" aria-hidden="true">
        {sources.map((item, index) => <g key={item.label}><path d={incoming(index)} className={`routing-wire ${source === index ? "selected-wire" : ""}`}/><circle cx="216" cy={index === 0 ? 92 : 198} r="3" className={source === index ? "selected-port" : "wire-port"}/></g>)}
        {clients.map((item, index) => <g key={item.label}><path d={outgoing(index)} className={`routing-wire ${client === index ? "selected-wire" : ""}`}/><circle cx="784" cy={[42, 145, 248][index]} r="3" className={client === index ? "selected-port" : "wire-port"}/></g>)}
        <path d={incoming(source)} pathLength="1" className="route-pulse incoming-pulse"/>
        <path d={outgoing(client)} pathLength="1" className="route-pulse outgoing-pulse"/>
      </svg>
      <div className="source-list">{sources.map((item, index) => <button key={item.label} aria-pressed={source === index} className={`source ${source === index ? "active" : ""}`} onClick={() => { setSource(index); setTrace(value => value + 1) }}><item.icon size={20}/><span>{item.label}</span><span className="source-dot"/></button>)}</div>
      <div className="gateway"><span className="device-screw top-left"/><span className="device-screw top-right"/><Mark/><strong>switchboard</strong><div><code>find</code><ArrowRight size={13}/><code>run</code></div><span className="device-caption">POWERED BY HYDRADB</span><span className="device-screw bottom-left"/><span className="device-screw bottom-right"/></div>
      <div className="client-list">{clients.map((item, index) => <button key={item.label} aria-pressed={client === index} className={`source client-source ${client === index ? "active" : ""}`} onClick={() => { setClient(index); setTrace(value => value + 1) }}><item.icon size={20}/><span>{item.label}</span><ArrowUpRight size={13}/></button>)}</div>
    </div>
    <div className="route-status" role="status" aria-live="polite"><span className="route-status-dot"/>{sources[source].label}<ArrowRight size={12}/><span>Switchboard</span><ArrowRight size={12}/>{clients[client].label}<span className="trace-count">{trace > 0 ? `TRACE ${String(trace).padStart(2, "0")}` : "SELECT A ROUTE"}</span></div>
    <div className="architecture-caption"><span>01 — CONNECT</span><p>An architectural illustration. Your registered servers determine what’s available.</p><span>02 — EXECUTE</span></div>
  </div>
}
export default function App() {
  return <div className="site">
    <a className="skip-link" href="#main">Skip to content</a>
    <header className="header">
      <a href="#" className="brand" aria-label="Switchboard home"><Mark/><span>switchboard</span></a>
      <nav aria-label="Main navigation"><a href="#how-it-works">How it works</a><a href={`${repository}#install`} target="_blank" rel="noreferrer">Documentation <ArrowUpRight size={12}/></a><a className="github-link" href={repository} target="_blank" rel="noreferrer">GitHub <ArrowUpRight size={14}/></a></nav>
    </header>
    <main id="main">
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <div className="eyebrow"><span/> THE UNIVERSAL MCP GATEWAY</div>
          <h1 id="hero-title">All your tools.<br/><span>One connection.</span></h1>
          <p className="hero-description">Give your agent a task, not a tool catalog.<br/>Switchboard finds the path and runs it.</p>
          <div className="hero-actions"><SetupDialog/><WorkflowDialog/></div>
          <div className="hero-note"><span className="note-cross" aria-hidden="true">+</span><span>LOCAL FIRST<span className="note-divider">/</span>OPEN SOURCE<span className="note-divider">/</span>MCP NATIVE</span></div>
        </div>
        <figure className="signal-art">
          <div className="art-registration" aria-hidden="true"><span>FIG. 01</span><span>DEPENDENCIES → DIRECTION</span></div>
          <img src={`${import.meta.env.BASE_URL}signal-knot.svg`} width="640" height="640" fetchPriority="high" alt="Interwoven loops rendered in black and white dithered pixels"/>
          <figcaption><span>COMPLEXITY, CONNECTED.</span><span aria-hidden="true">[ 01 — ∞ ]</span></figcaption>
        </figure>
      </section>
      <div className="signal-strip" aria-label="Local gateway workflow"><span>A SMALLER SURFACE.<br/>A CLEARER PATH.</span><div><code>find</code><ArrowRight size={24}/><code>run</code></div><p>Two tools for your agent.<br/>Your whole network behind them.</p><a href="#how-it-works" aria-label="See how Switchboard works"><ArrowDown size={20}/></a></div>
      <section className="how-section" id="how-it-works" aria-labelledby="how-title">
        <div className="section-heading"><span className="kicker">01 / FROM CONTEXT TO CAPABILITY</span><span className="section-index" aria-hidden="true">[ THE SYSTEM ]</span></div>
        <div className="how-intro"><h2 id="how-title">Less context.<br/><span>More capability.</span></h2><p>The tools stay behind the gateway. Your agent gets an ordered plan, with each step connected to the next.<br/><br/>One place to connect. One path to execute.</p></div>
        <RoutingMap/>
        <div className="process-list">{workflow.map((item, index) => <article className="process" key={item.name}><span className="process-number">0{index+1} /</span><div><h3>{item.label}</h3><p>{item.description}</p></div><code>{item.name}<ArrowUpRight size={15}/></code></article>)}</div>
      </section>
      <section className="closing" aria-labelledby="closing-title"><div><span className="kicker">BUILT IN THE OPEN</span><h2 id="closing-title">Make the connection.</h2><p>Your tools. Your machine. A clearer way through.</p></div><a href={repository} target="_blank" rel="noreferrer">Explore on GitHub <ArrowUpRight size={20}/></a></section>
    </main>
    <footer><a href="#" className="brand" aria-label="Switchboard home"><Mark/><span>switchboard</span></a><span>BUILT WITH HYDRADB</span><a href={`${repository}/blob/main/docs/RESULTS.md`} target="_blank" rel="noreferrer">Results & methodology <ArrowUpRight size={12}/></a></footer>
  </div>
}

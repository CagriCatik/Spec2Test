"""In-app parameter reference shown by Help > Parameter reference."""

PARAMETER_DOC = """
<h1>Parameter reference</h1>

<h2>Input</h2>
<ul>
  <li><b>Specification</b><br>
    One or more PDF, TXT or Markdown documents. Requirements are detected by
    their ID pattern; a requirement-like paragraph without an ID receives a
    stable internal ID and is marked for a boundary review. Every requirement
    keeps the position it came from, so each draft can be traced back.</li>
  <li><b>Guidance file</b><br>
    Optional project text: role, coverage wishes, terminology. It is appended
    to the generation rules and cannot change the output format. A guidance
    file that demands a different JSON shape would make every answer invalid,
    which is why the rules always take precedence.</li>
</ul>

<h2>Endpoint</h2>
<ul>
  <li><b>Mode</b><br>
    <i>ollama_local</i> talks to a local Ollama daemon. If that daemon is
    signed in to ollama.com, cloud models run through it and no API key is
    needed here.<br>
    <i>ollama_cloud</i> talks to https://ollama.com directly and needs
    OLLAMA_API_KEY in the environment, for example through a .env file.<br>
    The mode never changes by itself: a cloud failure does not become a local
    run, and no error path starts the ollama command line tool.</li>
  <li><b>Host</b><br>
    Overrides the default host of the mode. Empty means https://ollama.com for
    cloud and http://localhost:11434 for local operation.</li>
  <li><b>Connect timeout</b><br>
    How long establishing the connection may take.</li>
  <li><b>Read timeout</b><br>
    How long one answer may take. The job budget shortens it when less time is
    left than the timeout allows.</li>
</ul>

<h2>Model</h2>
<ul>
  <li><b>Name</b><br>
    The name belongs to the mode. Local operation uses the name that
    <code>ollama list</code> shows, for example gpt-oss:120b-cloud. The cloud
    API uses the name that /api/tags reports, for example gpt-oss:120b. The two
    namespaces are never translated automatically; a mismatch is reported as a
    warning.</li>
  <li><b>Temperature</b><br>
    0 keeps the answer close to the most likely wording, higher values vary it.
    Low values suit structured output; 0.2 is a reasonable start.</li>
  <li><b>Top-p</b><br>
    Nucleus sampling: only the smallest set of tokens whose probability sums to
    p is considered.</li>
  <li><b>Top-k</b><br>
    Restricts sampling to the k most likely tokens.</li>
  <li><b>Max tokens</b><br>
    Upper bound for the answer. Too small a value truncates the JSON; a
    truncated answer is detected and rejected rather than parsed half way.</li>
  <li><b>Hardware</b><br>
    GPU and thread settings apply to local operation only. In cloud mode they
    are disabled, because the server decides how it computes.</li>
</ul>

<h2>Generation</h2>
<ul>
  <li><b>Max drafts per requirement</b><br>
    A resource ceiling, not a quota. The application enforces it: if the model
    returns more drafts, the surplus is not accepted and the run records a
    finding. Fewer drafts than the ceiling is a correct result.</li>
  <li><b>Repair attempts</b><br>
    An answer that fails validation is sent back with the original requirement,
    the previous answer and the concrete findings. After this many attempts the
    affected requirement is rejected visibly instead of being dropped.</li>
  <li><b>Requirements per request</b><br>
    1 sends one request per requirement, which keeps traceability simplest.
    Larger groups reduce the number of requests but mix contexts.</li>
  <li><b>Job budget</b><br>
    Total runtime for the whole run. When it runs out, requirements that were
    never started are listed explicitly and the run is reported as partial,
    never as completed.</li>
</ul>

<h2>Reading the result</h2>
<ul>
  <li><b>Test drafts</b><br>
    Every draft starts as <i>draft</i>. Only a deliberate decision by a tester
    makes a test case approved; the model never approves its own output.</li>
  <li><b>Requirements</b><br>
    What was captured, with the source position and the clarification status.
    Identical requirement IDs in different documents stay separate.</li>
  <li><b>Findings</b><br>
    Validation results, open questions, and requirements that were rejected or
    never started. Missing limits, DTCs, signal names and measurement methods
    are never invented: they appear here as open questions.</li>
  <li><b>Progress</b><br>
    100 % means the processing ended. Whether it worked is stated by the run
    status: completed, partial, failed or cancelled.</li>
</ul>

<h2>Configuration</h2>
<p>
  config.yml is read when the window opens. Changes made here apply to the
  current session; the file is only written when you choose
  <i>File &gt; Write settings to config.yml</i>. The API key is never stored in
  the file and never written to the log.
</p>
"""

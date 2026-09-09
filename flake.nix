# flake.nix
# THE TOOLCHAIN THIS PROJECT NEEDS, DECLARED.
#
# Environment as Code. Every tool a gate depends on is resolved from a locked
# nixpkgs revision, identically on a laptop and on a CI runner. Nothing here
# comes from PATH, Homebrew, or whatever the machine happened to have.
#
# TWO NAMES DIFFER FROM THE COMMON REGISTRY NAMES, found by searching nixpkgs
# rather than assuming:
#   opa  -> open-policy-agent  (the binary is still `opa`)
#   java -> temurin-bin-17     (NOT jdk17 -- a different build is a different
#                               tool, and the Databricks CLI expects Temurin)
{
  description = "cscie103 OLTP-to-OLAP data platform toolchain";

  inputs = {
    # UNSTABLE, WHICH IS THE RIGHT BRANCH FOR A DEV TOOLCHAIN.
    #
    # A release branch is FROZEN at release and receives only security
    # backports, so its packages go stale by design. The sibling project pinned
    # nixos-26.05 and measured the cost: uv four minor versions behind, and a
    # databricks CLI from a SUPERSEDED MAJOR LINE (0.290 against 1.14).
    #
    # The reproducibility a release branch is chosen for comes from flake.lock,
    # not the branch. The lock records an exact revision; the branch only
    # decides where `nix flake update` moves TO. Nothing changes until someone
    # updates the lock deliberately.
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      inherit (nixpkgs) lib;

      # THE THREE DOUBLES. policies/machine/nix validates against this same set,
      # so the flake and the policy agree on what "supported" means.
      #
      # NO x86_64-darwin. Nixpkgs 26.11 dropped it outright -- evaluation fails
      # with an error, not a warning -- following Apple's announcement that
      # macOS 26 is the last version for Intel Macs.
      systems = [ "aarch64-darwin" "aarch64-linux" "x86_64-linux" ];

      # ONE UNFREE PACKAGE, NAMED, AND NOTHING ELSE.
      #
      # databricks-cli carries a proprietary licence, so nixpkgs refuses to
      # REALISE it. `nix flake check --no-build` does not notice: --no-build
      # proves the flake EVALUATES and nothing more. The refusal appears on the
      # first real `nix develop`, after the gate reported green.
      #
      # NOT allowUnfree = true, and NOT NIXPKGS_ALLOW_UNFREE. The blanket flag
      # permits every unfree package present and future, so a dependency
      # acquiring one later passes silently. The environment variable is worse:
      # it requires --impure, reintroducing the ambient state this project
      # exists to exclude.
      pkgsFor = lib.genAttrs systems (system:
        import nixpkgs {
          inherit system;
          config.allowUnfreePredicate = pkg: lib.getName pkg == "databricks-cli";
        });

      # genAttrs rather than flake-utils: one fewer input to lock and update,
      # for a function that is three lines.
      forAllSystems = f: lib.genAttrs systems (system: f pkgsFor.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          # THE ONLY DECLARATION OF THE TOOLCHAIN. There is no [tools] block in
          # mise.toml: two declarations of one fact is the drift this project
          # exists to remove.
          packages = with pkgs; [
            betterleaks        # secret scanning
            databricks-cli     # bundle validate / deploy
            gh                 # PR and ruleset operations
            jq                 # JSON assertions in gates
            lefthook           # git hooks
            open-policy-agent  # policy as code
            osv-scanner        # dependency vulnerabilities
            regal              # Rego linting
            temurin-bin-17     # JVM for the Databricks CLI and local Spark
            uv                 # Python dependency resolution
            zizmor             # GitHub Actions workflow auditing
          ];
        };
      });

      # SOMETHING FOR `nix flake check` TO VERIFY, so the flake is more than
      # parseable. Building the shell's inputs proves every package above
      # actually resolves on that platform -- a name that exists on Linux and
      # not Darwin fails here rather than on someone's laptop.
      checks = forAllSystems (pkgs: {
        toolchain-resolves = pkgs.runCommand "toolchain-resolves"
          {
            buildInputs =
              self.devShells.${pkgs.stdenv.hostPlatform.system}.default.buildInputs;
          }
          "echo ${pkgs.stdenv.hostPlatform.system} > $out";
      });
    };
}

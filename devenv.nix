{ pkgs, ... }:

{
  # https://devenv.sh/languages/python/
  languages.python = {
    enable = true;
    package = pkgs.python314;
    venv.enable = true;
    uv = {
      enable = true;
      sync = {
        enable = true;
        allGroups = true;
      };
    };
  };
}

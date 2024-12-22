{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  packages = [
    (pkgs.python3.withPackages (python-pkgs: with python-pkgs; [
      requests
      selenium
    ]))
    pkgs.chromedriver
    pkgs.chromium
  ];
}
